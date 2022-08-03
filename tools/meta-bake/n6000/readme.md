# HPS Boot Image for N6000 #
## Introduction ##

### Current Agilex/HPS Boot ###
Traditionally, the Hard Processor System (HPS) on Agilex based FPGAs are
configured to boot from a supported flash device (SD card, QSPI flash, eMMC, NAND).
There are two configurations to support these boot options.

  * FPGA First

    This mode requires that the SDM configures the FPGA first before loading the
    HPS First Stage Boot Loader and taking the HPS out of reset.

  * HPS First

    With this mode, the SDM configures and initiates the HPS boot process so that
    the HPS can configure the FPGA once it is fully booted.

For more details about these boot flows, see the
[boot user guide](https://www.intel.com/content/dam/www/programmable/us/en/pdfs/literature/ug/ug-agilex-soc-boot.pdf)

### N6000 Boot Overview ###
With the introduction of the N6000 ADP, a new "FPGA First" boot flow has been introduced
that requires that the FSBL poll on a given register before continuing to boot the HPS.
Once this register indicates it is ready, the FSBL will load a monolithic u-boot FIT image
at a given offset. This image is made up of the following components:

* u-boot bootloader also referred to as the Second Stage Boot Loader (SSBL)
* A Linux Kernel image (traditionally called "Image")
* The root filesystem (rootfs) consisting of kernel modules as well as user space software


# Implementation #

## Components ##
The following table describes the components that enable the N6000 HPS boot flow.

 Component       | Description |
-----------------|-------------|
 copy-engine IP  | IP designed to transfer a u-boot FIT image to a given location in HPS DDR.
 host driver SW  | SW designed to drive copy engine IP given user input/configuration.
 modified FSBL   | Programmed into FPGA modified to poll on copy engine registers and load u-boot image from DDR.
 modified u-boot | Bootloader with a modified device tree and initialization code for the Zarlink chip.
 modified Kernel Device Tree | Linux device tree of N6000 platform with uio device mapped to copy engine registers.
 rootfs with SW | Linux filesystem that includes "copy-engine" daemon designed to communicate with copy engine IP via uio device.

## Yocto Build ##
Yocto is an open source toolkit used to create Linux distributions and commonly used for creating Linux images
and bootloaders for embedded environments. A Yocto build environment is made up of one or more layers with each
layer consisting of recipes that get processed to build/install components in a layer. The workhorse of a Yocto
build is the program called `bitbake`. This program processes the recipes to compile and build packages and images
for the target platform. For SoC platforms, like the HPS, the ARM cross compiler is required.

See [here](https://www.yoctoproject.org) for more information regarding Yocto.
Several reference designs found in rocketboards.org use Yocto for building the Linux image and/or bootloader.
For the N6000 image and boot flow, the Yocto build
[script](https://releases.rocketboards.org/release/2021.04/gsrd/tools/create-linux-distro-release) for the
[Agilex SoC Golden System Reference Design](https://rocketboards.org/foswiki/Documentation/AgilexSoCGSRD) has
been adapted to automate building the boot loader, Linux Image, and filesystem needed to support the N6000 device.

### The Build Script ###
The build script used for the Agilex SoC GSRD, create-linux-distro-release, is a bash script that automates the build
of Linux images of different types (gsrd, nand, etc.) that are compatible with a target FPGA platform (agilex, stratix10, etc.).
In general, the script pulls Yocto layers/recipes from public repositories and configures a Yocto build environment to
build an image for a supported FPGA platform. The following table lists the remote repositories hosting Yocto meta data
source used by this script as well as source code used for building binaries that make up the Linux image (kernel and rootfs).

Repository | Description
-----------|------------
https://git.yoctoproject.org/git/poky.git | Base build tools and meta data layers
https://git.openembedded.org/meta-openembedded | Layers for OE-core packages
https://git.yoctoproject.org/git/meta-intel-fpga | Meta data layers for Intel FPGA SoC platforms
https://git.yoctoproject.org/git/meta-intel-fpga-refdes | BSP layer for Intel SoCFPGA GSRD
https://github.com/altera-opensource/linux-socfpga | Linux kernel source repository for socfpga
https://github.com/altera-opensource/u-boot-socfpga | u-boot bootloader source repository for socfpga

Recipes in the meta-intel-fpga-refdes layer mostly inherit from and extend recipes in other layers.
The following table lists the new or modified recipes (in meta-intel-fpga-refdes) necessary to support N6000 boot image.

Component | Recipe | Description
----------|--------|------------
Linux Kernel | recipes-kernel/linux/linux-socfpga-lts_5.10.bbappend | Recipe to fetch and build socfpga Linux 5.10. Modified to support N6000 device tree (.dtb).
u-boot | recipes-bsp/u-boot/u-boot-socfpga_v2021.07.bbappend | Recipe to fetch and build socfpga u-boot. Modified to support N6000 in u-boot. This also creates a shell script, *mkuboot-fit.sh.
copy-engine | recipes-bsp/copy-engine/copy-engine-0.1.bb | New recipe to build copy-engine daemon in rootfs.
n6000 image | recipes-images/poky/n6000-image-minimal.bb | New recipe to create the N6000 image with copy-engine and linuxptp packages installed.

*mkuboot-fit.sh is meant to be called after a Yocto build to create the u-boot FIT image for N6000. This is a workaround for the Yocto
build order which builds the bootloader (u-boot) before building the Linux image rootfs. Because the rootfs is part of the u-boot FIT
image, the rootfs must be built before building the bootloader. The result of calling this script is copying the rootfs (as a .cpio file)
to the u-boot source file tree and calling `make` in the u-boot build tree. When called again with the rootfs present, the resulting image
will contain the rootfs.

### Environment Setup ###
As mentioned before, the ARM cross compiler must be installed in order to build images for SoC platforms.
Following recommended setup instructions from rocketboards.org for Agilex SoC GSRD, the ARM cross toolchain
can be setup like the following example.
```bash
wget https://developer.arm.com/-/media/Files/downloads/gnu-a/10.2-2020.11/binrel/gcc-arm-10.2-2020.11-x86_64-aarch64-none-linux-gnu.tar.xz
tar xf gcc-arm-10.2-2020.11-x86_64-aarch64-none-linux-gnu.tar.xz
rm gcc-arm-10.2-2020.11-x86_64-aarch64-none-linux-gnu.tar.xz
export CROSS_COMPILE=`pwd`/gcc-arm-10.2-2020.11-x86_64-aarch64-none-linux-gnu/bin/aarch64-none-linux-gnu-
export ARCH=arm64
```

The `poky` tools include `bitbake` so it is not necessary to install it separately.

### Running the Script ###
Once the modified `create-linux-distro-release` has been downloaded, the N6000 u-boot FIT image can be built
following the example below. The `-f build` argument in this example is used to indicate the directory to use
for the Yocto workspace and artifacts. It is recommended that this location have at least 100Gb free. It is
important to note that any downloaded source file will be erased and downloaded again with every subsequent
invocation of this script.

```bash
./create-linux-distro-release -t agilex -f build -i n6000
```

All image files will be copied to `build/agilex-n6000-images`.
The u-boot.itb file to use for N6000 is `build/agilex-n6000-images/u-boot.itb`.
The FSBL will be in the *file `build/agilex-n6000-images/u-boot-agilex-socdk-n6000-atf/u-boot-spl-dtb.hex`

*Although the directory contains `atf` (for ARM Trusted Firmware), the current u-boot binaries built for n6000
will not use ATF.

### Customizations for N6000 ###
The following is a list or customizations made for building the n6000 image for Agilex platforms.
These customizations are all being done in the meta-intel-fpga-refdes layer. Currently, these changes
exist only in a branch of the private
[repository](https://gitlab.devtools.intel.com:29418/psg-opensource/meta-intel-fpga-refdes.git)
hosted within Intel. Once all changes for the Linux kernel as well as u-boot are merged in their public
repositories, this branch will be modified accordingly before being submitted for approval to merge
into the main branch for the meta-intel-fpga-refdes repository. Once this is merged into its public
counterpart, the build script will have to be modified to reflect that.

#### Extending the u-boot recipe ####
A recipe extension file (recipes-bsb/u-boot/u-boot-socfpga_v2021.07.bbapend) has been added to the meta-intel-fpga-refdes layer
that does the following:
* Add patches using Yocto's patching mechanism
* Introduces a new u-boot config, socfpga_agilex_n6000_defconfig, and associates it with a keyword, `agilex-n6000`, that can be
referenced in Yocto configuration files. These patches are necessary until those changes are merged into the public u-boot-socfpga
repository.
* Creates mkuboot-fit.sh script file with variables for u-boot source and build directories that will get expanded
to the actual paths that Yocto uses for fetching/building u-boot.
Along with this recipe file, relevant patch files have been added. Once the changes are in the u-boot repository, the patches and
any references to them must be removed.

#### Patching Linux Kernel ####
The kernel extension recipe, recipes-kernel/linux/linux-socfpga-lts_5.10.bbappend, in the meta-intel-fpga-refdes layer, has been
modified to add a patch file using Yocto's patching mechanism. This patch file adds the device tree for N6000 and is only
necessary until this change is merged into the public linux-socfpga repository.

#### Adding Custom User Space Software ####
A new recipe, recipes-bsp/copy-engine-0.1.bb and relevant source files, have been added to the meta-intel-fpga-refdes layer.
This recipe includes instructions for building the copy-engine program as well as installing it as a systemd service.
Yocto will build this into an RPM package that gets installed into any image that includes it in the IMAGE_INSTALL variable.
This recipe may be used as a guide for installing additional user space software.

#### Creating an Image Type ####
A new recipes, recipes-images/poky/n6000-image-minimal.bb, has been added that includes directives to install the copy-engine
package (built in this layer) as well as the linuxptp package (available in other layers). In addition to including these
packages, this recipe includes a rootfs post processing command that removes the Linux kernel image files from the rootfs.
This is done because the Linux kernel is part of the u-boot FIT image and therefore not used from the rootfs. Removing this
redundant file reduces the final u-boot FIT image by about 30Kb. This recipe may be modified or used as a guide to add additional
user space software.

### Testing and Debugging ###
As mentioned previously, the script will erase source files every time it is executed. This means that any changes
made locally will be lost when the script is run again after making these changes. The example below shows how to
test local changes without executing the script again.
```bash
cd build
source poky/oe-init-build-env agilex-gsrd-rootfs/
bitbake n6000-image-minimal
./agilex-n6000-rootfs/tmp/deploy/images/agilex/mkuboot-fit.sh
```

# Booting N6000 #
As mentioned before, this boot flow is an FPGA-first boot flow which requires that the Agilex based FPGA be configured with
the necessary components (SPL/FSBL, copy-engine) in order for the HPS to boot.
## Example Boot ##
This example assumes the following preconditions have been met prior to booting HPS:
* A SOF file synthesized with the SPL (u-boot-spl-dtb.hex).
* Copy engine IP with relevant registers accessible to host and HPS.

Once the host FPGA boots with the required bitstream, the SPL in the HPS will begin polling a register in the copy engine.
One way to get an indication that the HPS is ready to continue past the SPL is to use a terminal emulator on a host with a
serial cable connected to the FPGA's UART port.

To transfer the u-boot FIT image, use the `hps` program with 'cpeng' subcommand from the host.
Note, the `hps` program can be installed as part of installing the OPAE SDK suite of packages.
See [here](https://github.com/OPAE/opae-sdk/tree/master/ofs/apps/cpeng#readme) for information on running the `hps` program.

```bash
hps cpeng -f u-boot.itb
```

This will transfer the u-boot FIT image via the copy engine IP to the HPS DDR and then signal completion of the transfer to the
copy engine. Once the copy engine completes the actual transfer, it will write to the register the HPS SPL is polling on allowing
the SPL to load the u-boot bootloader which will in turn boot into the Linux image embedded in the u-boot FIT image.
If a terminal emulator is connected to the UART as described above, a user can observe u-boot and Linux running on the HPS.

## Addition of meta-bake.py ##
A script called meta-bake.py has been added to allow for more control of configuration/customization of recipes and their dependencies.
This script separates the data from the logic by requiring that data be expressed in a yaml configuration file. This file contains the
following confiration data:
* machine - The FPGA/SoC platform to build for, choices are agilex, stratix10, arria10, cyclone5
* image - The image type to build, choices are gsrd, nand, pcie, pr, qsqpi, sgmii, tse, n6000
* target - The build target to build. This is typically a Yocto image to build.
* fit - Make a monolothic FIT image after the Yocto build. This will use u-boot source and binaries as well as the rootfs made for the image.
* repos - A list of repositories to pull for Yocto recipes. This information is made up of:
  * name - The project name (this is also the directory where source is clone to)
  * url - The URL to pull the source from
  * branch - The branch to checkout
  * add_layers - Can be either True or a list of sub-directories to add as layers in bblayers.conf
  * patch - Path to a file to use to patch the source code
  * keep - When set to true, this will leave the source tree untouched on subsequent runs
* upstream_versions - Dependencies/versions to use for either Linux kernel, u-boot, and/or ATF. This information is made up of:
  * name - Project name
  * version - version to configure recipes that use it
  * branch - branch to use, will use git hash in recipe
  * url - URL to pull the source from
  * disabled - when set to True, this project will be ignored
* local - Used to configure local.conf used by Yocto/bitbake build. This information is made up of:
  * remove - List of keys to remove from local.conf
  * values - Dictionary of key/value pairs to use to insert into local.conf. Any existing key/value pairs will be overwritten.


