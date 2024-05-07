import json
import logging
import pathlib
import re
import subprocess
import sys
from os import listdir
from os.path import isfile, join, isdir

logger = logging.getLogger(__name__)


def _check_sudo():
    result = subprocess.run(['sudo', '-n', 'true'])
    if result.returncode != 0:
        logger.error("Could not execute sudo, please check id user is allowed to use sudo")
        sys.exit(1)


class Mount(object):

    def __init__(self, path, mount_root_path):
        self.path = path
        self.mount_root_path = mount_root_path
        self.losetup_data = None
        self.loop_device = None
        self.scan_filter = None
        self.renamed_vg_suffix = None
        self.lv_devices = []
        _check_sudo()
        self._get_losetup_data()

    def mount(self):
        if self._is_loop_device_setup():
            logger.info(f"Image {self.path} already setup as loop device {self.loop_device}")
        else:
            self._setup_loop_device()

        self._scan_lvm_lvs()
        self._mount_devices()

    def unmount(self):
        if self._is_loop_device_setup():

            volume_group = self._scan_lvm_vg()

            result = subprocess.run(["mount", "-v"], capture_output=True,
                                    text=True)

            if result.returncode == 0:
                mount_points = [f.split()[0] for f in result.stdout.splitlines() if self.renamed_vg_suffix in f]

                for mount_point in mount_points:
                    result_unmount = subprocess.run(["sudo", "umount", mount_point], capture_output=True,
                                                    text=True)

                    if result_unmount.returncode != 0:
                        logger.error(f"Could not unmount {mount_point}: {result_unmount.stderr}")
                        sys.exit(1)

                    logger.info(f"Unmounted {mount_point}")

            logger.info(f"Deactivating volume group {volume_group}")
            self._change_vg_active(volume_group, "n")

            logger.info(f"Restoring volume groups name: {volume_group.replace(self.renamed_vg_suffix, '')}")
            self._restore_lvm_vg(volume_group)

            logger.info(f"Detaching {self.loop_device}")
            self._detach_loop_device()

        else:
            logger.info(f"Nothing to unmount from previous runs")

    def _get_losetup_data(self):
        result = subprocess.run(["sudo", "/usr/sbin/losetup", "--list", "--json"], capture_output=True,
                                text=True)
        if result.returncode == 0:
            self.losetup_data = json.loads(result.stdout)
        else:
            logger.error(f"Error while getting losetup data: {result.stderr}")
            sys.exit(1)

    def _is_loop_device_setup(self):
        if "loopdevices" in self.losetup_data:
            for data in self.losetup_data["loopdevices"]:
                if data["back-file"] == self.path:
                    self._set_loop_device(data["name"])
                    return True
        return False

    def _setup_loop_device(self):
        result = subprocess.run([
            "sudo", "losetup", "--partscan", "--find", "--show", self.path],
            capture_output=True,
            text=True)

        if result.returncode == 0:
            self._set_loop_device(result.stdout.strip())
            logger.info(f"Setup {self.path} as {self.loop_device}")
        else:
            logger.error(f"Could not setup image {self.path} as loop device: {result.stderr}")

    def _detach_loop_device(self):
        result = subprocess.run([
            "sudo", "losetup", "--detach", self.loop_device],
            capture_output=True,
            text=True)

        if result.returncode == 0:
            logger.info(f"Detached {self.path} from {self.loop_device}")
        else:
            logger.error(f"Could not detach image {self.path} from loop device {self.loop_device}: {result.stderr}")

    def _set_loop_device(self, loop_device):
        self.loop_device = loop_device
        self.scan_filter = "devices{filter=[\"a|^" + self.loop_device + "[^0-9].*$|\",\"r|.*|\"]}"
        self.renamed_vg_suffix = self.loop_device.replace("/", "_")

    def _scan_lvm_vg(self):
        result = subprocess.run(
            ["sudo", "vgscan", "--config", self.scan_filter],
            capture_output=True,
            text=True)

        # TODO: handle multiple volume groups
        #
        if result.returncode == 0:
            match = re.match(pattern=r'^.*volume group "([^"]+)"', string=result.stdout.strip(), flags=re.IGNORECASE)
            if match and match.group(1):
                volume_group = match.group(1)
                logger.info(f"Found volume group: {volume_group}")
                return volume_group

        return None

    def _scan_lvm_lvs(self):
        logger.info(f"Scanning LVM on {self.loop_device}")

        volume_group = self._scan_lvm_vg()

        if volume_group:
            self._rename_lvm_vg(volume_group)
            self._change_vg_active(volume_group, "y")

            result = subprocess.run(
                ["sudo", "lvscan", "--config", self.scan_filter],
                capture_output=True,
                text=True)

            if result.returncode != 0:
                logger.error(f"Could not scan LVs of volume group {volume_group}: {result.stderr}")
                sys.exit(1)

            for lv_data in result.stdout.split("\n"):
                if "ACTIVE" in lv_data:
                    lv_device = lv_data.split()[1].replace("'", "")
                    self.lv_devices.append(lv_device)
                    logger.info(f"Found LV device {lv_device}")

    def _mount_devices(self):

        for lv_device in self.lv_devices:
            mount_path = f"{self.mount_root_path}/{lv_device}"
            pathlib.Path(mount_path).mkdir(exist_ok=True, parents=True)

            result = subprocess.run(
                ["sudo", "mount", lv_device, mount_path],
                capture_output=True,
                text=True)

            if result.returncode != 0:
                if "already mounted" in result.stderr:
                    logger.info(f"Device {lv_device} already mounted to {mount_path}")
                    continue
                if "unknown filesystem type" in result.stderr:
                    logger.info(f"Skipping device {lv_device}: unknown file system type")
                    continue

                else:
                    logger.error(f"Could not mount device {lv_device} to {mount_path}: {result.stderr}")
                    sys.exit(1)

            logger.info(f"Mounted {lv_device} to {mount_path}")

    def _change_vg_active(self, volume_group, active):
        logger.info(f"Changing volume group {volume_group} active status: {active}")
        result = subprocess.run(
            ["sudo", "vgchange", "--config", self.scan_filter, "--activate", active],
            capture_output=True,
            text=True)

        if result.returncode != 0:
            logger.error(f"Could not change active status of volume group {volume_group}: {result.stderr}")
            sys.exit(1)

    def _rename_lvm_vg(self, volume_group):
        logger.info(f"Renaming volume group {volume_group} to {volume_group}_{self.renamed_vg_suffix}")

        result = subprocess.run(
            ["sudo", "vgrename", "--config", self.scan_filter, volume_group,
             f"{volume_group}_{self.renamed_vg_suffix}"],
            capture_output=True,
            text=True)

        if result.returncode != 0:
            logger.error(f"Could not rename volume group {volume_group}: {result.stderr}")
            sys.exit(1)

    def _restore_lvm_vg(self, volume_group):
        volume_group_clean = volume_group.replace(f"_{self.renamed_vg_suffix}", "")
        if volume_group_clean == volume_group:
            return

        logger.info(f"Restoring volume group {volume_group} to {volume_group_clean}")

        result = subprocess.run(
            ["sudo", "vgrename", "--config", self.scan_filter,
             volume_group, volume_group_clean],
            capture_output=True,
            text=True)

        if result.returncode != 0:
            logger.error(f"Could not rename volume group {volume_group}: {result.stderr}")
            sys.exit(1)

    def _unmount_image(self):
        pass
