import argparse
import logging
import sys
import confuse
from confuse import ConfigReadError

import migrator

logger = logging.getLogger("main")

if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="VMWare to OpenStack move script",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("-c", "--config", help="Config file to use", required=True)
    parser.add_argument(
        "-n",
        "--name",
        help="Name of the SCS server to create during migration",
        required=True,
    )
    parser.add_argument(
        "-fc",
        "--forceCopy",
        action="store_true",
        help="Force copying of image files from ESXI if already present in data directory",
    )
    # TODO
    # parser.add_argument("-m", "--mount", action="store_true", help="Mount raw images after converting and stop")

    args = parser.parse_args()
    options = vars(args)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s:%(levelname)s - %(message)s"
    )
    logger.info("Starting")

    try:
        config = confuse.Configuration("vm2os", __name__)
        config.set_file(options["config"])
    except ConfigReadError as error:
        logger.error(
            f"Could not read configuration {options['config']}: {error.reason}"
        )
        sys.exit(1)

    migrator = migrator.Migrator(config=config, name=options["name"], arguments=options)
    migrator.initialize()
    migrator.poweroff_vm()
    migrator.copy_images()
    migrator.convert_images()
    migrator.import_images()
    migrator.create_server()
