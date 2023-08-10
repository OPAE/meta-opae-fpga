#!/usr/bin/env python3
# Copyright(c) 2022-2023, Intel Corporation
#
# Redistribution  and  use  in source  and  binary  forms,  with  or  without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of  source code  must retain the  above copyright notice,
#   this list of conditions and the following disclaimer.
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# * Neither the name  of Intel Corporation  nor the names of its contributors
#   may be used to  endorse or promote  products derived  from this  software
#   without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING,  BUT NOT LIMITED TO,  THE
# IMPLIED WARRANTIES OF  MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT  SHALL THE COPYRIGHT OWNER  OR CONTRIBUTORS BE
# LIABLE  FOR  ANY  DIRECT,  INDIRECT,  INCIDENTAL,  SPECIAL,  EXEMPLARY,  OR
# CONSEQUENTIAL  DAMAGES  (INCLUDING,  BUT  NOT LIMITED  TO,  PROCUREMENT  OF
# SUBSTITUTE GOODS OR SERVICES;  LOSS OF USE,  DATA, OR PROFITS;  OR BUSINESS
# INTERRUPTION)  HOWEVER CAUSED  AND ON ANY THEORY  OF LIABILITY,  WHETHER IN
# CONTRACT,  STRICT LIABILITY,  OR TORT  (INCLUDING NEGLIGENCE  OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE,  EVEN I
import argparse
import logging
import re
import shutil
import subprocess
import sys
import yaml
import os
from pathlib import Path

LOG = logging.getLogger()

GIT_UPDATE = '''
git checkout master
git branch -D {branch} || true
git fetch origin
git pull
git checkout {branch}
'''.strip()


GIT_CLONE = '''
git clone -b {branch} {url} {srcdir}
'''.strip()

GIT_LSREMOTE = '''
git ls-remote {url} {branch}
'''.strip()

YOCTO_BUILD = '''
source {rootfs_dir}/conf/local.conf {build_dir}
bitbake virtual/kernel -c cleanall
'''

YOCTO_CLEANUP = '''
rm -rf {rootfs_dir}/tmp
rm -rf {rootfs_dir}/conf
rm -rf {images_dir}/*
'''


class dot_version:
    VERSION_RE = re.compile('(?P<version>\d+(?:\.\d+)*)')

    def __init__(self, version: str):
        self.str_version = version
        self.num_version = [int(n) for n in version.split('.')]

    def __str__(self):
        return self.str_version

    @classmethod
    def get(cls, s: str):
        for v in cls.VERSION_RE.findall(s):
            yield cls(v)


class git_repo:
    URL_RE = re.compile('(?P<scheme>[\w\+]+)://(?P<fqdn>[\w\.-]+)/(?P<path>.*?)/(?P<name>[\w-]+)(\.git)?')

    def __init__(self, **kwargs):
        def get_url_tail(url):
            m = self.URL_RE.match(url)
            if m:
                return m.group('name')
        self.url = kwargs['url']
        self.name = kwargs.get('name', get_url_tail(self.url))
        self.branch = kwargs.get('branch', 'master')
        self.topdir = kwargs.get('topdir', 'build')
        self.srcdir = Path(self.topdir, self.name)

    def update(self):
        cmd = GIT_UPDATE.format(branch=self.branch)
        subprocess.call(['/bin/bash', '-c', cmd], cwd=self.srcdir)

    def clone(self):
        cmd = GIT_CLONE.format(branch=self.branch,
                               url=self.url,
                               srcdir=self.srcdir.stem)
        subprocess.call(['/bin/bash', '-c', cmd], cwd=self.topdir)

    def remote_hash(self):
        cmd = GIT_LSREMOTE.format(url=self.url,
                                  branch=self.branch)
        try:
            output = subprocess.check_output(['/bin/bash', '-c', cmd])
        except subprocess.CalledProcessError:
            output = None
        if output:
            return output.strip().split()[0]

    def patch(self, patchfile):
        cmd = f'patch -N -p1 < {patchfile}'
        try:
            subprocess.check_call(['/bin/bash', '-c', cmd], cwd=self.srcdir)
        except subprocess.CalledProcessError as err:
            LOG.error(f'error appling patch {patchfile} to {self.srcdir}')


class bitbaker:
    var_assign_re = re.compile(r'^(:?export)?\s*(\w+)="\s*([\w_\-/\.\+]+)\s*"\s*$',
                               re.MULTILINE)

    def __init__(self, poky_dir: Path, rootfs_dir: Path, build_dir: Path) -> None:
        self.poky_dir = poky_dir.absolute()
        self.rootfs_dir = rootfs_dir.absolute()
        self.build_dir = build_dir.absolute()
        oe_init_build_env = self.poky_dir.joinpath('oe-init-build-env')
        self.source_cmd = f'source {oe_init_build_env} {self.rootfs_dir}'

    def with_env(self, *cmds):
        script = '\n'.join([self.source_cmd] + list(cmds))
        return script

    def run(self, *cmds, **kwargs):
        script = self.with_env(*cmds)
        cmd = ['/bin/bash', '-c', script]
        cwd = kwargs.pop('cwd', self.build_dir)
        LOG.info(f'cwd: {cwd}')
        LOG.info(f'cmd: {" ".join(cmd)}')
        return subprocess.call(cmd, cwd=cwd)

    def initialize(self, cfg: dict, args: argparse.Namespace, ctx: dict):
        # run once to create the conf files
        self.run()

        self.update_local_conf(cfg, ctx)
        self.update_bblayers(cfg)

    def update_local_conf(self, cfg: dict, ctx: dict):
        local_conf = self.rootfs_dir.joinpath('conf/local.conf')
        with local_conf.open('r') as fp:
            conf_text = fp.read()

        def resolve(m):
            key = m.group('word')
            return ctx.get(key, f'${key}')
        local_cfg = cfg.get('local', {})
        for key in local_cfg.get('remove', []):
            conf_text = re.sub(f'${key}.*=.*', '', conf_text)

        for k, v in local_cfg.get('values', {}).items():
            if v is None:
                conf_text = re.sub(f'${k}.*=.*', '', conf_text)
            else:
                if v.startswith('+='):
                    op = "+="
                    v = v.strip("+=")
                else:
                    op = "="
                value = re.sub('\$(?P<word>[\w_-]+)', resolve, v)
                if re.findall(f'^{k}\s+\+?=.*', conf_text, re.MULTILINE):
                    conf_text = re.sub(f'{k}.*?=.*', f'{k} {op} "{value}"',
                                       conf_text, re.MULTILINE)
                else:
                    conf_text += f'{k} {op} "{value}"\n'

        conf_text = re.sub('\$(?P<word>[\w_-]+)', resolve, conf_text)
        # TODO: add "reguire/conf/machine/*-extra.conf" to conf_text
        with local_conf.open('w') as fp:
            fp.write(conf_text)

    def update_bblayers(self, cfg: dict):
        repos = cfg.get('repos', [])

        bblayers_conf = self.rootfs_dir.joinpath('conf/bblayers.conf')
        with bblayers_conf.open('r') as fp:
            bblayers_text = fp.read()
        for repo in repos:
            add_layers = repo.get('add_layers')
            if add_layers is None:
                continue
            name = repo['name']
            prefix = f'BBLAYERS += " ${{TOPDIR}}/../{name}'
            if isinstance(add_layers, list):
                bblayers_add = [f'{prefix}/{l} "' for l in add_layers]
            else:
                bblayers_add = [f'{prefix} "']
            for line in bblayers_add:
                if line not in bblayers_text:
                    bblayers_text += f'{line}\n'
        with bblayers_conf.open('w') as fp:
            fp.write(bblayers_text)

    def build(self, target, clean=False):
        if clean:
            cmds = ['bitbake virtual/kernel -c cleanall',
                    'bitbake u-boot-socfpga -c cleanall',
                    'bitbake hw-ref-design -c cleanall']
        else:
            cmds = []
        cmds.append(f'bitbake {target}')
        cmds.append(f'bitbake u-boot-socfpga')
        self.run(*cmds)

    def get_target_env(self, target):
        script = self.with_env(f'bitbake -e {target}')
        data = {}
        try:
            output = subprocess.check_output(
                ['/bin/bash', '-c', script], cwd=self.build_dir).decode()
        except subprocess.CalledProcessError:
            return data
        for m in self.var_assign_re.finditer(output):
            data[m.group(2)] = m.group(3)
        return data

    def make_fit(self, target, ctx: dict):
        LOG.info('getting environment for uboot')
        uboot_env = self.get_target_env('virtual/bootloader')
        LOG.info(f'getting environment for {target}')
        image_env = self.get_target_env(target)
        uboot_src_dir = uboot_env['S']
        uboot_bin_dir = uboot_env['B']
        uboot_config = 'socfpga_agilex_n6000_defconfig'.format(**ctx)
        uboot_make_dir = Path(uboot_bin_dir, uboot_config)
        print(f'uboot_src={uboot_src_dir}')
        print(f'uboot_bin={uboot_bin_dir}')
        cpio = '{DEPLOY_DIR_IMAGE}/{IMAGE_BASENAME}-{machine}.cpio'.format(**image_env, **ctx)
        print(f'cpio={cpio}')
        #shutil.copy(cpio, uboot_make_dir.joinpath('rootfs.cpio'))
        LOG.debug(f'uboot_make_dir is {uboot_make_dir}')
        #subprocess.run(['make'], cwd=uboot_make_dir)
        #LOG.debug("running bitbake u-boot-socfpga")
        #subprocess.run(['bitbake', 'u-boot-socfpga'])
	
        itb = uboot_make_dir.joinpath('u-boot.itb')
        spl = uboot_make_dir.joinpath('spl/u-boot-spl-dtb.hex')
        return (itb, spl)

    def vab(self, cfg: dict, repos: dict, ctx: dict, args: argparse.Namespace, target, images_dir):
        LOG.info(' uboot vab')
        # clone fcs_prepare repo

        fcs_prepare = repos.get('fcs_prepare')
        print("fcs_prepare:", fcs_prepare)
        if fcs_prepare is None:
            LOG.error('No fcs_prepare repo specified')
            return False

        # Build fcs_prepare
        print(" fcs_prepare.srcdir", fcs_prepare.srcdir)
        subprocess.run(['make'], cwd=fcs_prepare.srcdir)
        fcs_prepare = os.path.abspath('{}/{}/{}'.format(os.getcwd(),
                                                        fcs_prepare.srcdir, "fcs_prepare"))
        print("fcs_prepare:", fcs_prepare)

        uboot_dtb = cfg.get('uboot-dtb')
        linux_binary = cfg.get('linux-binary')
        print("uboot-dtb:", uboot_dtb)
        print("linux_binary:", linux_binary)
        print("qaurtus-path:", args.quartus)
        print("build_dir:", args.build_dir)

        # create vab build dir
        build_vab = os.path.abspath('{}/{}'.format(args.build_dir, "vab"))
        print("build_vab:", build_vab)
        if os.path.exists(build_vab):
            shutil.rmtree(build_vab)
        os.mkdir(build_vab)

        # remove uboot-socfpga-vab-n6000 dir
        uboot_vab = os.path.abspath('{}/{}'.format(args.build_dir, "uboot-socfpga-vab-n6000"))
        print("uboot_vab:", uboot_vab)
        if os.path.exists(uboot_vab):
            shutil.rmtree(uboot_vab)

        # uboot env
        LOG.info('getting environment for uboot')
        uboot_env = self.get_target_env('virtual/bootloader')
        LOG.info(f'getting environment for {target}')
        image_env = self.get_target_env(target)
        uboot_src_dir = uboot_env['S']
        uboot_bin_dir = uboot_env['B']
        uboot_config = 'socfpga_{machine}_{image}_defconfig'.format(**ctx)
        uboot_make_dir = Path(uboot_bin_dir, uboot_config)
        print(f'uboot_src={uboot_src_dir}')
        print(f'uboot_bin={uboot_bin_dir}')
        print(f'uboot_make_dir={uboot_make_dir}')

        # copy uboot src to folder uboot-socfpga-vab-n6000
        shutil.copytree(uboot_src_dir, uboot_vab)

        for fname in linux_binary:
            print(fname)
            # copy unsinged files
            print("path ", uboot_make_dir.joinpath(fname))
            shutil.copy(uboot_make_dir.joinpath(fname), build_vab)

        # quartus path
        quartus_path = os.path.abspath('{}/{}'.format(args.quartus,
                                                      "bin/quartus_sign"))
        if not os.path.exists(quartus_path):
            print(" Invalid quartus_path:", quartus_path)
            return -1
        print(" quartus_path:", quartus_path)

        root_public_qky = os.path.abspath('{}/../{}/{}'.format(args.conf.name,
                                                               "vab", cfg.get('root-public-qky')))
        print("root_public_qky:", root_public_qky)
        if not os.path.exists(root_public_qky):
            print(" Invalid root_public_qky:", root_public_qky)
            return False

        root_private_pem = os.path.abspath('{}/../{}/{}'.format(args.conf.name,
                                                                "vab", cfg.get('root-private-pem')))
        print("root_private_pem:", root_private_pem)
        if not os.path.exists(root_private_pem):
            print(" Invalid root_private_pem:", root_private_pem)
            return False

        root_public_pem = os.path.abspath('{}/../{}/{}'.format(args.conf.name,
                                                               "vab", cfg.get('root-public-pem')))
        print("root_public_pem:", root_public_pem)
        if not os.path.exists(root_public_pem):
            print(" Invalid root_public_pem:", root_public_pem)
            return False

        # build u-boot
        ret_value = subprocess.run(["make", "clean"], cwd=uboot_vab)
        if not ret_value:
            print("Failed to make clean uboot soruce code ", ret_value)

        ret_value = subprocess.run(["make", "mrproper"], cwd=uboot_vab)
        if not ret_value:
            print("Failed to make mrproper uboot soruce code ", ret_value)

        ret_value = subprocess.run(["make", "socfpga_agilex_n6000_vab_defconfig"], cwd=uboot_vab)
        if not ret_value:
            print("Failed to make socfpga_agilex_n6000_vab_defconfig ", ret_value)

        ret_value = subprocess.run(["make", "-j"], cwd=uboot_vab)
        if not ret_value:
            print("Failed to make uboot ", ret_value)

        for fname in uboot_dtb:
            print(fname)
            # copy unsinged bin files
            shutil.copy(str(uboot_vab + "/" + fname), build_vab)

        hps_binary = uboot_dtb + linux_binary
        # Sign uboot binary
        for fname in hps_binary:
            print(fname)

            # delete unsigned_cert.cert
            unsigned_cert_path = build_vab + "/" + "unsigned_cert.ccert"
            print("unsigned_cert_path:", unsigned_cert_path)
            if os.path.exists(unsigned_cert_path):
                os.remove(unsigned_cert_path)

            # create unsigned_cert.ccert
            p1 = subprocess.check_call(args=[fcs_prepare,
                                       '--hps_cert', fname, '-v'], cwd=build_vab)

            # create signed cert
            signed_cert = 'signed_cert{}.ccert'.format(fname)
            # print(" signed_cert:",signed_cert)
            signed_cert_path = build_vab + "/" + signed_cert
            p1 = subprocess.check_call(args=[quartus_path, '--family=agilex',
                                       '--operation=SIGN', str('--qky=' + root_public_qky),
                                       str('--pem=' + root_private_pem), unsigned_cert_path,
                                       signed_cert], cwd=build_vab)

            # Sign binary
            p1 = subprocess.check_call(args=[fcs_prepare, '--finish', signed_cert_path,
                                       '--imagefile', fname], cwd=build_vab)

            signed_bin = 'signed-{}'.format(fname)
            # print(" signed_bin:",signed_bin)
            hps_image_signed = build_vab + "/" + "hps_image_signed.vab"
            if os.path.exists(hps_image_signed):
                shutil.move(hps_image_signed, build_vab + "/" + signed_bin)

            if os.path.exists(signed_cert_path):
                os.remove(signed_cert_path)
            if os.path.exists(unsigned_cert_path):
                os.remove(unsigned_cert_path)

            print(build_vab + "/" + signed_bin)
            shutil.copy(build_vab + "/" + signed_bin, uboot_vab)

        # Build VAB enabled uboot
        # print("uboot_vab:", uboot_vab)
        ret_value = subprocess.run(["make", "socfpga_agilex_n6000_vab_defconfig"], cwd=uboot_vab)
        if not ret_value:
            print("Failed to make socfpga_agilex_n6000_vab_defconfig ", ret_value)

        ret_value = subprocess.run(["make", "-j"], cwd=uboot_vab)
        if not ret_value:
            print("Failed to make uboot ", ret_value)

        # print("images_dir ",images_dir)
        uboot_userkey_vab = os.path.abspath('{}/{}'.format(images_dir,
                                                           "u-boot-vab.itb"))

        uboot_stl_vab = os.path.abspath('{}/{}'.format(images_dir,
                                                       "u-boot-spl-dtb-vab.hex"))

        # print("uboot_userkey_vab ",uboot_userkey_vab)
        # print("uboot_stl_vab ",uboot_stl_vab)
        shutil.copy(uboot_vab + "/" + "u-boot.itb", uboot_userkey_vab)
        shutil.copy(uboot_vab + "/spl/u-boot-spl-dtb.hex", uboot_stl_vab)

        return True


def get_meta(cfg: dict, args: argparse.Namespace) -> dict:
    repos = {}
    for r in cfg.get('repos', []):
        name = r['name']
        keep = r.pop('keep', False) or name in args.keep
        repo = git_repo(**r, topdir=args.build_dir)
        patch = r.get('patch')
        if repo.srcdir.exists():
            if not keep:
                repo.update()
            else:
                patch = None
        else:
            repo.clone()

        if patch is not None:
            patch_file = Path(patch)
            conf_dir = Path(args.conf.name).absolute().parent
            if not patch_file.exists():
                patch_file = conf_dir.joinpath(patch)
            if patch_file.exists():
                repo.patch(patch_file.absolute())
            else:
                LOG.warn(f'patch file, {patch}, not found')
        repos[name] = repo
    return repos


def find_bb_file(name: str, version_str: str, meta_project: git_repo, bb_dir: str) -> Path:
    bb_files = meta_project.srcdir.joinpath(bb_dir)
    bb_file = bb_files.joinpath(f'{name}-v{version_str}.bb')
    if bb_file.exists():
        return bb_file
    else:
        version = dot_version(version_str)
        bb_matches = list(bb_files.glob(f'{name}?v*.bb'))
        for bb_file in bb_matches:
            versions = list(dot_version.get(str(bb_file)))
            if versions:
                version_tpl = versions[0].num_version
                if version.num_version[:len(version_tpl)] == version_tpl:
                    return bb_file
    # can't find file with version, use last one
    last = sorted(bb_matches)[-1]
    LOG.warn(f'cannot find recipe named for version {version_str}, using {last.stem}')
    return last


def insert_hash_srcrev(ext_project: dict, meta_project: git_repo, version_field: str, bb_dir: str):
    name = ext_project['name']
    version_str = str(ext_project['version'])
    bb_file = find_bb_file(name, version_str, meta_project, bb_dir)
    if bb_file:
        repo = git_repo(**ext_project)
        h = repo.remote_hash().decode()
        with bb_file.open('r') as fp:
            text = re.sub('SRCREV = .*', f'SRCREV = "{h}"', fp.read())
            text = re.sub(f'{version_field} = .*', f'{version_field} = "v{version_str}"', text)
        with bb_file.open('w') as fp:
            fp.write(text)


def update_meta(cfg: dict, repos: dict, args: argparse.Namespace):
    intel_fpga = repos['meta-intel-fpga']
    v_info = cfg.get('ingredients', {})
    linux_version_info = v_info.get('linux', {})
    uboot_version_info = v_info.get('uboot', {})
    atf_version_info = v_info.get('atf', {})

    linux_version = dot_version(linux_version_info.get('version', ''))
    if linux_version.num_version == (5, 4):
        insert_hash_srcrev(linux_version_info,
                           intel_meta,
                           'LINUX_VERSION',
                           'recipes-kernel/linux')

    if uboot_version_info and not uboot_version_info.pop('disabled', False):
        insert_hash_srcrev(uboot_version_info,
                           intel_fpga,
                           'UBOOT_VERSION',
                           'recipes-bsp/u-boot')
    if atf_version_info and not atf_version_info.pop('disabled', False):
        insert_hash_srcrev(atf_version_info,
                           intel_fpga,
                           'ATF_VERSION',
                           'recipes-bsp/arm-trusted-firmware')


IMAGE_TYPES = ['gsrd', 'nand', 'pcie', 'pr', 'qspi', 'sgmii', 'tse', 'n6000']
MACHINES = ['agilex', 'stratix10', 'arria10', 'cyclone5']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('build_dir', default='build', type=Path)
    parser.add_argument('--conf', type=argparse.FileType('r'), default='layers.yaml')
    parser.add_argument('--keep', nargs='+', default=[])
    parser.add_argument('--no-cleanup', action='store_true', default=False)
    parser.add_argument('--machine', choices=MACHINES, default=None)
    parser.add_argument('--image', choices=IMAGE_TYPES, default=None)
    parser.add_argument('--target', default=None)
    parser.add_argument('--images-dir', type=Path)
    parser.add_argument('--skip-build', action='store_true', default=False)
    parser.add_argument('--quartus', type=Path)
    parser.add_argument('--vab', action='store_true', default=False)

    args = parser.parse_args()

    fmt = logging.Formatter('[%(asctime)s][%(levelname)s] %(message)s')
    stdout_h = logging.StreamHandler(sys.stdout)
    stdout_h.setFormatter(fmt)
    LOG.setLevel(logging.DEBUG)
    LOG.addHandler(stdout_h)
    if not args.build_dir.exists():
        args.build_dir.mkdir()
    cfg = yaml.safe_load(args.conf)

    machine = args.machine or cfg.get('machine')
    if machine is None:
        raise SystemExit('no machine specified in cfg or command line')

    image = args.image or cfg.get('image')
    if image is None:
        raise SystemExit('no image specified in cfg or command line')

    target = args.target or cfg.get('target')
    if target is None:
        raise SystemExit('no target specified in cfg or command line')

    rootfs_dir = args.build_dir.joinpath(f'{machine}-{image}-rootfs')
    images_dir = args.images_dir or args.build_dir.joinpath(f'{machine}-{image}-images')
    if not args.no_cleanup:
        cmd = YOCTO_CLEANUP.format(rootfs_dir=rootfs_dir,
                                   images_dir=images_dir)
        LOG.info(f'cleaning up with commands:\n{cmd}')
        subprocess.call(['/bin/bash', '-c', cmd])

    ctx = {'machine': machine,
           'image': image,
           'target': target,
           'build_dir': str(args.build_dir.absolute())}
    repos = get_meta(cfg, args)
    poky = repos.get('poky')
    if poky is None:
        LOG.error('No poky repo specified')
        raise SystemExit('No poky repo specified')
    update_meta(cfg, repos, args)

    bb = bitbaker(poky.srcdir, rootfs_dir, args.build_dir)
    bb.initialize(cfg, args, ctx)
    if args.skip_build:
        raise SystemExit(0)
    try:
        bb.build(target)
    except subprocess.CalledProcessError as err:
        raise SystemExit(err)

    if not images_dir.exists():
        images_dir.mkdir()
    # VAB signing
    # make fit
    if not args.vab:
        if cfg.get('fit', False):
            itb, spl = bb.make_fit(target, ctx)
            shutil.copy(itb, images_dir)
            shutil.copy(spl, images_dir)
    else:
        # VAB
        try:
            itb, spl = bb.make_fit(target, ctx)
            shutil.copy(itb, images_dir)
            shutil.copy(spl, images_dir)
            bb.vab(cfg, repos, ctx, args, target, images_dir)
        except subprocess.CalledProcessError as err:
            raise SystemExit(err)

    # packaging


if __name__ == '__main__':
    main()
