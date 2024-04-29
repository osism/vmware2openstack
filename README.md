# VMware to OpenStack migration script (vmware2openstack)

This script may be used to migrate VMs from VMware to
an OpenStack environment.

The script itself must be run on a machine which has access to:
- the ESXI host where the VM to migrated resides on
- the OpenStack environment where the VM needs to migrated to

## Usage

```shell
usage: vmware2scs.py [-h] -c CONFIG -n NAME [-fc]

VMWare to OpenStack move script

options:
  -h, --help            show this help message and exit
  -c CONFIG, --config CONFIG
                        Config file to use (default: None)
  -n NAME, --name NAME  Name of the server to create during migration (default: None)
  -fc, --forceCopy      Force copying of image files from ESXI if already present in data directory (default: False)
```

At least a configuration file and the name of the server
must be given.

The server will be migrated into the configured OpenStack
environment using the name given at command line.

## Configuration

### Example configuration

```yaml
converter:
  # path on converter host to store vmdk files
  # and converted raw images
  data_path: /convert/vmware2scs

esxi:
  # the ESXI host
  host: vmhost05.example.com
  
  # ESXI password
  password: nudelsuppe
  
  # ESXI ssh port
  ssh-port: 22
  
  # Name of virtual machine to migrate
  vm: glados-42

openstack:
  
  target:
    # Target flavor
    flavor: SCS-8V-16
    
    # Target security group
    security_group: 13371337-12f1-47e4-ac7a-e3a2a8813ca6
    
    # Target networks (name: <IP>)
    networks:
      - somenet: auto

  env:
    # Target openstack environment
    OS_AUTH_TYPE: password
    OS_AUTH_URL: https://your.keystone.example.com:32443/v3
    OS_IDENTITY_API_VERSION: 3
    OS_INTERFACE: public
    OS_PASSWORD: 1337
    OS_PROJECT_DOMAIN_NAME: Default
    OS_PROJECT_NAME: someproject
    OS_PROJECT_ID: 133713375cc54a1e8ea17dc27d77af2a
    OS_USERNAME: chell
    OS_USER_DOMAIN_NAME: Default
    OS_VOLUME_API_VERSION: 3
    OS_REGION_NAME: Testchamber23

```

## Example migration

```shell
(venv) conv-user@converter:~/vmware2openstack$ python3 main.py -c etc/migrate-glados.yaml -n glados
2024-04-23 13:25:48,808 - main:INFO - Starting
2024-04-23 13:25:48,810 - migrator:INFO - Creating migration directory at /convert/vmware2openstack/glados
2024-04-23 13:25:48,992 - migrator:INFO - Found VM glados-42 on ESXI host esxi.example.com with id 42 and path /vmfs/volumes/13371337-1584da60-0d47-80c16e72faa0/glados-42
2024-04-23 13:25:48,992 - migrator:INFO - Checking ssh connection to ESXI host
2024-04-23 13:25:49,274 - migrator:INFO - Openstack: network show somenet
2024-04-23 13:25:50,100 - migrator:INFO - Openstack: security group show 13371337-12f1-47e4-ac7a-e3a2a8813ca6

W A R N I N G ! ! !
        
The virtual machine glados-42 (42) will be shutdown after you proceed.    
Be very sure from here on.
        
Proceed? [y/N] y
2024-04-23 13:25:56,447 - migrator:INFO - VM powered off successfully
2024-04-23 13:25:56,448 - migrator:INFO - Copying vmdk files from vmhost05.a.uintra.net:/vmfs/volumes/536f131e-1584da60-0d47-80c16e72faa0/nxc-sat06dt-02 to /convert/vmware2openstack/mig2
2024-04-23 13:25:58,808 - migrator:INFO - Progress: /convert/vmware2openstack/glados/glados-42-disc0-flat.vmdk: 0%
...
2024-04-23 13:30:38,822 - migrator:INFO - Progress: /convert/vmware2openstack/glados/glados-42-disc0-flat.vmdk: 100%
2024-04-23 13:30:48,822 - migrator:INFO - Progress: /convert/vmware2openstack/glados/glados-42-disc1-flat.vmdk: 0%
...
2024-04-23 13:35:28,838 - migrator:INFO - Progress: /convert/vmware2openstack/glados/glados-42-disc1-flat.vmdk: 100%
2024-04-23 13:35:33,109 - migrator:INFO - Converting vmdk files in /convert/vmware2openstack/glados
2024-04-23 13:35:33,110 - migrator:INFO - Converting /convert/vmware2openstack/glados/glados-42-disc0.vmdk
    (100.00/100%)
2024-04-23 13:36:37,727 - migrator:INFO - Converting /convert/vmware2openstack/glados/glados-42-disc1.vmdk
    (100.00/100%)
2024-04-23 13:37:36,658 - migrator:INFO - Openstack: image list
2024-04-23 13:37:37,981 - migrator:INFO - Skipping import of glados-42-disc0.vmdk.raw: already imported as 13371337-3863-49b2-aa0f-16bd7711d76e
2024-04-23 13:37:37,981 - migrator:INFO - Openstack: image show 13371337-3863-49b2-aa0f-16bd7711d76e
2024-04-23 13:37:38,787 - migrator:INFO - Skipping import of glados-42-disc1.vmdk.raw: already imported as 13371337-f86c-4bbd-8cc8-65853c022278
2024-04-23 13:37:38,787 - migrator:INFO - Openstack: image show 13371337-f86c-4bbd-8cc8-65853c022278
2024-04-23 13:37:39,583 - migrator:INFO - Creating server with the following settings:
2024-04-23 13:37:39,583 - migrator:INFO -     Flavor: SCS-8V-16
2024-04-23 13:37:39,583 - migrator:INFO -     Security group: 13371337-12f1-47e4-ac7a-e3a2a8813ca6
2024-04-23 13:37:39,583 - migrator:INFO -     Network: somenet with IP auto
2024-04-23 13:37:39,583 - migrator:INFO -     Boot volume with image: 13371337-3863-49b2-aa0f-16bd7711d76e, size 20.0GB
2024-04-23 13:37:39,583 - migrator:INFO -     Additional volume with image: 13371337-f86c-4bbd-8cc8-65853c022278, size 20.0GB
2024-04-23 13:37:39,584 - migrator:INFO - Openstack: server create --flavor SCS-8V-16 --image 13371337-3863-49b2-aa0f-16bd7711d76e --boot-from-volume 20 --security-group 13371337-12f1-47e4-ac7a-e3a2a8813ca6 --nic net-id=13371337-b6ef-4f4e-b999-9d6cc6088a95 --block-device uuid=13371337-f86c-4bbd-8cc8-65853c022278,source_type=image,destination_type=volume,volume_size=20 --os-compute-api-version 2.90 glados
2024-04-23 13:37:40,940 - migrator:INFO - Openstack server created, have fun
```
