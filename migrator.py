import logging
import os
import pathlib
import re
import subprocess
import sys
import time
from os import listdir
from os.path import isfile, join

import paramiko
import yaml
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
from scp import SCPClient

logger = logging.getLogger(__name__)


class Colors:
    WARNING = '\033[93m'
    CRITICAL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


_scp_progress_time = time.time()


def _scp_progress(filename, size, sent):
    global _scp_progress_time
    if time.time() - _scp_progress_time > 10:
        logger.info(f"Progress: {filename.decode()}: {int(sent / size * 100)}%")
        _scp_progress_time = time.time()


class Migrator:

    def __init__(self, config, name, arguments):
        self.config = config
        self.name = name
        self.arguments = arguments
        self.data_dir = f"{self.config['converter']['data_path']}/{self.name}"
        self.esxi_host = self.config['esxi']['host'].get()
        self.esxi_ssh_port = self.config['esxi']['ssh-port'].get()
        self.esxi_ssh_password = self.config['esxi']['password'].get()
        self.esxi_vm = None
        self.esxi_vm_id = None
        self.esxi_vm_name = self.config['esxi']['vm'].get()
        self.esxi_datastores = {}
        self.esxi_vm_path = None
        self.esxi_instance = None
        self.flavor = self.config['openstack']['target']['flavor'].get()
        self.security_group = self.config['openstack']['target']['security_group'].get()
        self.security_group_id = None
        self.images = []
        self.networks = []

    def __del__(self):
        if type(self.esxi_instance) == "ServiceInstance":
            Disconnect(self.esxi_instance)

    def initialize(self):
        """
        Initialization

        - creates data directory
        - connects to ESXI
        - looks up source VM and configuration
        - checks for existence of openstack resources
        """
        logging.getLogger("paramiko").setLevel("WARNING")
        # create migration data directory
        #
        logger.info(f"Creating migration directory at {self.data_dir}")
        pathlib.Path(self.data_dir).mkdir(exist_ok=True)

        # check esxi and find vm
        # also store datastores - those are required to find the path where vmdk files reside in
        #
        self.esxi_instance = SmartConnect(host=self.esxi_host, user="root", pwd=self.esxi_ssh_password,
                                          disableSslCertValidation=True)

        content = self.esxi_instance.RetrieveContent()

        objview = content.viewManager.CreateContainerView(content.rootFolder,
                                                          [vim.HostSystem],
                                                          True)
        esxi_hosts = objview.view
        objview.Destroy()

        for esxi_host in esxi_hosts:
            storage_system = esxi_host.configManager.storageSystem
            host_file_sys_vol_mount_info = \
                storage_system.fileSystemVolumeInfo.mountInfo
            for child in host_file_sys_vol_mount_info:
                if hasattr(child, "mountInfo") and hasattr(child, "volume"):
                    self.esxi_datastores[child.volume.name] = child.mountInfo.path

        for child in content.rootFolder.childEntity:
            if hasattr(child, "vmFolder"):
                datacenter = child
                vm_folder = datacenter.vmFolder
                vm_list = vm_folder.childEntity
                for vm in vm_list:
                    if hasattr(vm, "summary"):
                        vm_name = vm.summary.config.name
                        if vm_name == self.esxi_vm_name:
                            self.esxi_vm = vm
                            self.esxi_vm_id = vm._GetMoId()
                            self.esxi_vm_path = vm.summary.config.vmPathName
                            for datastore in self.esxi_datastores.keys():
                                self.esxi_vm_path = self.esxi_vm_path.replace(datastore,
                                                                              self.esxi_datastores[datastore])

                            self.esxi_vm_path = re.sub(r"\[(.+)\] ", r"\1/", self.esxi_vm_path)
                            self.esxi_vm_path = os.path.dirname(os.path.normpath(self.esxi_vm_path))

        if not self.esxi_vm:
            logger.error(f"Could not find vm on esxi host {self.esxi_host}: {self.esxi_vm}")
            sys.exit(1)

        if not self.esxi_vm_path:
            logger.error(f"Could not determine VM path on ESXI host")
            sys.exit(1)

        logger.info(
            f"Found VM {self.esxi_vm.summary.config.name} on ESXI host {self.esxi_host} "
            f"with id {self.esxi_vm_id} and path {self.esxi_vm_path}")

        # check connection to esxi via ssh
        #
        logger.info(f"Checking ssh connection to ESXI host")
        try:
            ssh_client = self._create_ssh_client()
            ssh_client.exec_command("id")
            ssh_client.close()
        except paramiko.SSHException as e:
            logger.error(f"Could not connect to ESXi host: {e}")
            sys.exit(1)

        # do id lookups for given openstack resources
        #
        for network in self.config['openstack']['target']['networks'].get():
            (name, ip) = next(iter(network.items()))
            result_network, *_ = self.openstack_execute(f"network show {name}")
            if not result_network or not result_network['id']:
                logger.error(f"Cannot get id of network {name}")
                sys.exit(1)
            self.networks.append((name, result_network['id'], ip))

        result_security_group, *_ = self.openstack_execute(f"security group show {self.security_group}")
        if not result_security_group or not result_security_group['id']:
            logger.error(f"Cannot get id of security group {self.security_group}")
            sys.exit(1)
        self.security_group_id = result_security_group['id']

    def _create_ssh_client(self):
        """
        Create and connect SSH session

        :return: the ssh client
        """
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(self.esxi_host, port=self.esxi_ssh_port, username="root", password=self.esxi_ssh_password)
        return client

    def poweroff_vm(self):
        """
        Power off VM

        Issues a warning if VM is not yet powered off
        """

        # if vm is already powered off, all is fine
        #
        if self.esxi_vm.summary.runtime.powerState == "poweredOff":
            logger.info(f"VM {self.esxi_vm.summary.config.name} is already powered off")
            return

        print(f"""
{Colors.CRITICAL}{Colors.BOLD}W A R N I N G ! ! !{Colors.ENDC}
        
{Colors.CRITICAL}The virtual machine {Colors.WARNING}{Colors.BOLD}{self.esxi_vm.summary.config.name} ({self.esxi_vm_id}){Colors.ENDC}{Colors.CRITICAL} will be shutdown after you proceed.{Colors.ENDC}    
{Colors.CRITICAL}Be very sure from here on.{Colors.ENDC}
        """)

        answer = input(f"{Colors.CRITICAL}{Colors.BOLD}Proceed?{Colors.ENDC} {Colors.CRITICAL}[y/N]{Colors.ENDC} ")
        if answer.lower() != "y":
            sys.exit(2)

        # TODO: power off using python API
        # this will only work for at least "essential" licensed ESXI
        # task = self.esxi_vm.PowerOff()
        # wait_for_tasks(self.esxi_instance, tasks=[task])

        # power off using vim-cmd vmsvc/power.off
        # this will work without any license
        #
        ssh_client = self._create_ssh_client()
        stdin, stdout, stderr = ssh_client.exec_command(f"/bin/vim-cmd vmsvc/power.off {self.esxi_vm_id}")

        exit_code = stdout.channel.recv_exit_status()

        ssh_client.close()

        if exit_code != 0:
            logger.error("Received exit code > 0 while powering off VM")
            for line in stderr.readlines():
                logger.error(line)
            sys.exit(3)

        logger.info("VM powered off successfully")

    def copy_images(self):
        """
        Copy image files

        Copy vmdk image files from ESXI host to local data directory
        """

        vmdk_files = [f for f in listdir(self.data_dir)
                      if isfile(join(self.data_dir, f)) and re.match("^.*disc\\d+.vmdk$", f)]

        if len(vmdk_files) > 0 and not self.arguments['forceCopy']:
            logger.info(f"There are already vmdk files in {self.data_dir}, skipping (use --forceCopy to force copying)")
            return

        logger.info(f"Copying vmdk files from {self.esxi_host}:{self.esxi_vm_path} to {self.data_dir}")
        ssh_client = self._create_ssh_client()
        scp = SCPClient(ssh_client.get_transport(), sanitize=lambda x: x, progress=_scp_progress)
        scp.get(f"{self.esxi_vm_path}/*.vmdk", self.data_dir)

        scp.close()
        ssh_client.close()

    def convert_images(self):
        """
        Convert images

        Converts vmdk images to raw images using qemu
        """
        vmdk_dir = self.data_dir
        logger.info(f"Converting vmdk files in {vmdk_dir}")
        try:
            vmdk_files = [f for f in listdir(vmdk_dir)
                          if isfile(join(vmdk_dir, f)) and re.match("^.*disc\\d+.vmdk$", f)]
            for vmdk_file in vmdk_files:
                if os.path.isfile(f"{self.data_dir}/{vmdk_file}.raw"):
                    logger.info(f"Skipping, converted image {vmdk_dir}/{vmdk_file}.raw already exists")
                    continue
                logger.info(f"Converting {vmdk_dir}/{vmdk_file}")
                os.system(f"qemu-img convert -p -f vmdk -O raw {vmdk_dir}/{vmdk_file} {self.data_dir}/{vmdk_file}.raw")

        except FileNotFoundError as error:
            logger.error(f"Could not collect vmdk files: {error.strerror}")
            sys.exit(1)

    def mount_images(self):
        """
        Mount raw images

        Mounts raw images to enable user to edit files before importing images into openstack
        """
        # TODO
        # problem: mounting LVM from loop devices is difficult :)
        pass

    def unmount_images(self):
        """
        Unmount raw images
        """
        # TODO
        # problem: mounting LVM from loop devices is difficult :)
        pass

    def openstack_execute(self, command):
        """
        Execute command in openstack

        Executes the given command within the openstack environment defined in configuration.

        :param command: the command to execute
        :return: a 3-tuple of yaml-output of command, stdout and stderr
        """
        # TODO: use python openstack bindings directly
        #
        os_env = os.environ.copy()
        for key, value in self.config['openstack']['env'].get().items():
            os_env[key] = str(value)

        logger.info(f"Openstack: {command}")

        result = subprocess.run(["openstack"] + command.split(" ") + ["-f", "yaml"], capture_output=True, text=True,
                                env=os_env)

        result_yaml = yaml.safe_load(result.stdout)
        return result_yaml, result.stdout, result.stderr

    def import_images(self):
        """
        Import images

        Imports raw image files into openstack
        """
        raw_files = [f for f in listdir(self.data_dir)
                     if isfile(join(self.data_dir, f)) and re.match("^.*disc\\d+.vmdk.raw$", f)]

        # get imported images (to check if import is done already later)
        #
        result_imported, *_ = self.openstack_execute("image list")[:1]

        for raw_file in sorted(raw_files):

            # check if image is already imported
            #
            imported = False
            for result in result_imported:
                if result['Name'] == raw_file:
                    logger.info(f"Skipping import of {raw_file}: already imported as {result['ID']}")
                    imported = True
                    result_show, *_ = self.openstack_execute(f"image show {result['ID']}")

                    if result_show['size']:
                        self.images.append((result['ID'], result_show['size']))
                    else:
                        logger.error(f"Failed to get image id of already imported image {raw_file}")
                        sys.exit(1)

            if imported:
                continue

            logger.info(f"Importing {raw_file}")

            result_import, rso, rse = self.openstack_execute("image create "
                                                             "--property hw_disk_bus=scsi "
                                                             "--property hw_scsi_model=virtio-scsi "
                                                             "--property hw_watchdog_action=reset "
                                                             "--disk-format raw "
                                                             "--private "
                                                             f"--file {self.data_dir}/{raw_file} {raw_file}")

            if not result_import:
                logger.error(f"Import failed: {rse}")
                sys.exit(1)

            if result_import['ID']:
                self.images.append((result_import['ID'], os.stat(f"{self.data_dir}/{raw_file}").st_size))

    def create_server(self):
        """
        Create server within openstack environment

        Creates the server within openstack using the parameters given in the configuration
        file
        """
        if len(self.images) == 0:
            logger.error("Cannot create server: no images to use")
            sys.exit(1)

        logger.info(f"Creating server with the following settings:")
        logger.info(f"    Flavor: {self.flavor}")
        opt_flavor = f"--flavor {self.flavor}"

        logger.info(f"    Security group: {self.security_group_id}")
        opt_security_group = f"--security-group {self.security_group_id}"

        opt_networks = []
        for network in self.networks:
            name, network_id, ip = network
            logger.info(f"    Network: {name} with IP {ip}")
            opt_network = f"--nic net-id={network_id}"
            if ip != "auto":
                opt_network = f"{opt_network},v4-fixed-ip={ip}"
            opt_networks.append(opt_network)

        boot_id, size = self.images.pop()
        logger.info(f"    Boot volume with image: {boot_id}, size {size / 1024 / 1024 / 1024}GB")
        opt_boot_image = f"--image {boot_id} --boot-from-volume {int(size / 1024 / 1024 / 1024)}"

        opt_block_devices = []
        for image in self.images:
            image_id, size = image
            logger.info(f"    Additional volume with image: {image_id}, size {size / 1024 / 1024 / 1024}GB")
            opt_block_devices.append(f"--block-device uuid={image_id}"
                                     f",source_type=image"
                                     f",destination_type=volume"
                                     f",volume_size={int(size / 1024 / 1024 / 1024)}")

        openstack_command = f"server create {opt_flavor} {opt_boot_image} {opt_security_group} " \
                            f"{' '.join(opt_networks)} {' '.join(opt_block_devices)} " \
                            f"--os-compute-api-version 2.90 {self.name}"

        logger.debug(openstack_command)

        result_create, rso, rse = self.openstack_execute(openstack_command)

        if not result_create:
            logger.error(f"Could not create openstack server: {rse}")
            sys.exit(1)

        logger.debug(result_create)
        logger.info("Openstack server created, have fun")
