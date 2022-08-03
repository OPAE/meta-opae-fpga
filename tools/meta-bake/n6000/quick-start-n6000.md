# N6000 image creation

### Requirements ###
* Host PC with Linux. Ubuntu 20.04 was used for the development of the N6000 image
  * ARM cross compiler
    * set CROSS_COMPILE environment variable to: <path to cross compiler>/bin/aarch64-linux-gnu-
  * At least 60 Gb of sisk space


### Quick Start ###
To create an u-boot fit image for N6000 platforms, run the following command (with layers.yaml configured as shown below):
```
>./meta-bake.py build --conf n6000/layers.yaml
```

This will do the following:
* Parse layers.yaml for configuration to use for build
* Download recipe repositories (including poky) listed in `repos` secion of layers.yaml
  * Apply refdes-n6000 patch to meta-intel-fpga-refdes source tree
* Configure Yocto build in build directory
  * Source build/poky/oe-init-build-env passing in agilex-n6000-rootfs. This will initialize conf files.
  * Configure build/agilex-n6000-rootfs/conf/local.conf using values in `local` section of layers.yaml
    * _Note_: IMAGE_FSTYPES is configured to include `cpio`
  * Configure build/agilex-n6000-rootfs/conf/bblayers.conf using layer specification in `repos` section of layers.yaml
* Run Yocto build for target listed in layers.conf
  * Call `bitbake n6000-image-minimal`
* Get environment variables to locate rootfs cpio file as well as u-boot source and build directories
* Copy rootfs created by Yocto build for u-boot
  * Copy rootfs cpio file (n6000-image-minimal-agilex*.rootfs.cpio) to u-boot build directory for selected configuration (socfpga_agilex_n6000_defconfig)
* Call u-boot build in directory for selected configuration
* Copy FIT image (u-boot.itb) to images directory, build/agilex-n6000-images

## Required Changes ##
The patch file applied on top of the meta-intel-fpga-refdes repository introduces patches to:
* Add patch files so that Yocto can modify Linux kernel to add configuration for creating a device tree binary (DTB) compatible with N6000
* Add patch files so that Yocto can modify the bootloader in u-boot to support booting with the assistance of the copy engine IP
* Modify rootfs to include copy-engine daemon as well as other packages that can be useful for debug

These changes may eventually be merged into upstream repositories for Linux socfpga, u-boot socfpga, and meta-intel-fpga-rerdes.
Once all changes make their way into the repositories for the aforementioned projects, it will no longer be necessary to apply patches.

## Manual Build ##
One may use meta-bake.py to only pull down required repositories and configure a Yocto build environment by using the --skip-build command line argument.
To initiate a build after this, source poky/oe-init-build-env passing in a directory as the only argument.
This will set up the user's environment to be able to run bitbake.
To build the Yocto image, run `bitbake n6000-image-minimal`.
This will build all the components necessary to build a FIT image.
Once the build is complete, u-boot make system may be used to make the FIT.
The u-boot build directory for the selected configuration can be found in the Yocto build environment directory at:
``` bash
> cd tmp/work/agilex-poky-linux/u-boot-socfpga/1_v2021.07+gitAUTOINC+24e26ba4a0-r0/build/socfpga_agilex_n6000_defconfig
```
Once in this directory, ensure that the necessary files are present in here in order to assemble the FIT image (u-boot.itb)
```bash
> cp ../../../../../../deploy/images/agilex/n6000-image-minimal-agilex.cpio rootfs.cpio
> ls Image linux.dtb rootfs.cpio
Image  linux.dtb  rootfs.cpio
> make
```

## References ##

### layers.yaml ###

```yaml
machine: agilex
image: n6000
target: n6000-image-minimal
fit: true
repos:
  - name: poky
    url: https://git.yoctoproject.org/git/poky.git
    branch: hardknott
  - name: meta-intel-fpga
    url: https://git.yoctoproject.org/git/meta-intel-fpga.git
    branch: hardknott
    add_layers: true
  - name: meta-intel-fpga-refdes
    url: https://github.com/intel-innersource/os.linux.yocto.reference-design.meta-intel-fpga-refdes
    branch: hardknott
    patch: refdes-n6000.patch
    keep: true
    add_layers: true
  - name: meta-openembedded
    url: git://git.openembedded.org/meta-openembedded.git
    branch: hardknott
    add_layers:
      - meta-oe
      - meta-networking
      - meta-python
ingredients:
  linux:
    name: linux-socfpga
    version: '5.10.50'
    branch: socfpga-5.10-lts
    url: https://github.com/altera-opensource/linux-socfpga.git
  uboot:
    name: u-boot-socfpga
    version: '2021.07'
    branch: socfpga_v2021.07
    url: https://github.com/altera-opensource/u-boot-socfpga.git
  atf:
    disabled: true
    version: '2.4.1'
    branch: socfpga_v2.4.1
    url: https://github.com/altera-opensource/arm-trusted-firmware.git
local:
  remove:
    - MACHINE
    - UBOOT_CONFIG
    - IMAGE
    - SRC_URI
  values:
    MACHINE: $machine
    DL_DIR: $build_dir/downloads
    DISTRO_FEATURES_append: " systemd"
    VIRTUAL-RUNTIME_init_manager: systemd
    IMAGE_TYPE: $image
    IMAGE_FSTYPES: "+=cpio tar.gz"
    PREFERRED_PROVIDER_virtual/kernel: linux-socfpga-lts
    PREFERRED_VERSION_linux-socfpga-lts: 5.10%
    UBOOT_CONFIG: agilex-n6000
    PREFERRED_PROVIDER_virtual/bootloader: u-boot-socfpga
    PREFERRED_VERSION_u-boot-socfpga: v2021.07%
```
