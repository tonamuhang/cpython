#!/usr/bin/env python
# vim:fileencoding=utf-8

from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import ctypes.wintypes
import os
import re
import subprocess
import sys
from functools import lru_cache

is64bit = os.environ.get('PLATFORM') != 'x86'


# vcvars() {{{
VS_VERSION = '15.0'
COMN_TOOLS_VERSION = '150'

CSIDL_PROGRAM_FILES = 38
CSIDL_PROGRAM_FILESX86 = 42


@lru_cache()
def get_program_files_location(which=CSIDL_PROGRAM_FILESX86):
    SHGFP_TYPE_CURRENT = 0
    buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(0, CSIDL_PROGRAM_FILESX86, 0,
                                           SHGFP_TYPE_CURRENT, buf)
    return buf.value


@lru_cache()
def find_vswhere():
    for which in (CSIDL_PROGRAM_FILESX86, CSIDL_PROGRAM_FILES):
        root = get_program_files_location(which)
        vswhere = os.path.join(root, "Microsoft Visual Studio", "Installer",
                               "vswhere.exe")
        if os.path.exists(vswhere):
            return vswhere
    raise SystemExit('Could not find vswhere.exe')


def get_output(*cmd):
    return subprocess.check_output(cmd, encoding='mbcs', errors='strict')


@lru_cache()
def find_visual_studio(version=VS_VERSION):
    path = get_output(
        find_vswhere(),
        "-version", version,
        "-requires",
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
        "-property",
        "installationPath",
        "-products",
        "*"
    ).strip()
    return os.path.join(path, "VC", "Auxiliary", "Build")


@lru_cache()
def find_msbuild(version=VS_VERSION):
    return get_output(
        find_vswhere(),
        "-version", version,
        "-requires",
        "Microsoft.Component.MSBuild",
        "-find",
        r"MSBuild\**\Bin\MSBuild.exe"
    ).strip()


def find_vcvarsall():
    productdir = find_visual_studio()
    vcvarsall = os.path.join(productdir, "vcvarsall.bat")
    if os.path.isfile(vcvarsall):
        return vcvarsall
    raise SystemExit("Unable to find vcvarsall.bat in productdir: " +
                     productdir)


def remove_dups(variable):
    old_list = variable.split(os.pathsep)
    new_list = []
    for i in old_list:
        if i not in new_list:
            new_list.append(i)
    return os.pathsep.join(new_list)


def query_process(cmd, is64bit):
    if is64bit and 'PROGRAMFILES(x86)' not in os.environ:
        os.environ['PROGRAMFILES(x86)'] = get_program_files_location()
    result = {}
    popen = subprocess.Popen(cmd,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    try:
        stdout, stderr = popen.communicate()
        if popen.wait() != 0:
            raise RuntimeError(stderr.decode("mbcs"))

        stdout = stdout.decode("mbcs")
        for line in stdout.splitlines():
            if '=' not in line:
                continue
            line = line.strip()
            key, value = line.split('=', 1)
            key = key.lower()
            if key == 'path':
                if value.endswith(os.pathsep):
                    value = value[:-1]
                value = remove_dups(value)
            result[key] = value

    finally:
        popen.stdout.close()
        popen.stderr.close()
    return result


@lru_cache()
def query_vcvarsall(is64bit=True):
    plat = 'amd64' if is64bit else 'amd64_x86'
    vcvarsall = find_vcvarsall()
    env = query_process('"%s" %s & set' % (vcvarsall, plat), is64bit)

    def g(k):
        try:
            return env[k]
        except KeyError:
            return env[k.lower()]

    return {
        k: g(k)
        for k in (
            'PATH LIB INCLUDE LIBPATH WINDOWSSDKDIR'
            f' VS{COMN_TOOLS_VERSION}COMNTOOLS PLATFORM'
            ' UCRTVERSION UNIVERSALCRTSDKDIR VCTOOLSVERSION WINDOWSSDKDIR'
            ' WINDOWSSDKVERSION WINDOWSSDKVERBINPATH WINDOWSSDKBINPATH'
            ' VISUALSTUDIOVERSION VSCMD_ARG_HOST_ARCH VSCMD_ARG_TGT_ARCH'
        ).split()
    }
# }}}


def printf(*args, **kw):
    print(*args, **kw)
    sys.stdout.flush()


def sanitize_path():
    needed_paths = [r'C:\Windows\System32']
    executables = 'git.exe curl.exe svn.exe powershell.exe'.split()
    for p in os.environ['PATH'].split(os.pathsep):
        for x in tuple(executables):
            if os.path.exists(os.path.join(p, x)):
                needed_paths.append(p)
                executables.remove(x)
    os.environ['PATH'] = str(os.pathsep.join(needed_paths))


def vcenv():
    env = os.environ.copy()
    env.update(query_vcvarsall())
    return {str(k): str(v) for k, v in env.items()}


def replace_in_file(path, old, new, missing_ok=False):
    if isinstance(old, type('')):
        old = old.encode('utf-8')
    if isinstance(new, type('')):
        new = new.encode('utf-8')
    with open(path, 'r+b') as f:
        raw = f.read()
        if isinstance(old, bytes):
            nraw = raw.replace(old, new)
        else:
            nraw = old.sub(new, raw)
        if raw == nraw and not missing_ok:
            raise ValueError('Failed (pattern not found) to patch: ' + path)
        f.seek(0), f.truncate()
        f.write(nraw)


def build():
    sanitize_path()
    # Cant pass this as an argument because of windows' insane argument parsing
    replace_in_file('PCbuild\\build.bat', '%1', '"/p:PlatformToolset=v141"')
    # vcvarsall.bat causes the wrong MSBuild.exe to be found in PATH,
    # so prevent build.bat from calling it, since we have called it
    # already and fixed the paths, anyway.
    replace_in_file('PCbuild\\build.bat',
                    re.compile(br'^rem Setup the environment.*?\)',
                               re.MULTILINE | re.DOTALL),
                    'set MSBUILD=msbuild')
    cmd = ('PCbuild\\build.bat', '-e', '--no-tkinter', '--no-bsddb', '-c',
           'Release', '-m', '-p', ('x64' if is64bit else 'Win32'), '-v', '-t',
           'Build')
    printf(*cmd)
    p = subprocess.Popen(cmd, env=vcenv())
    raise SystemExit(p.wait())


def test():
    sanitize_path()
    cmd = ('PCbuild\\amd64\\python.exe', 'Lib/test/regrtest.py', '-w', '-u',
           'network,cpu,subprocess,urlfetch')
    printf(*cmd)
    p = subprocess.Popen(cmd)
    raise SystemExit(p.wait())


def main():
    q = sys.argv[-1]
    if q == 'build':
        build()
    elif q == 'test':
        test()
    else:
        if len(sys.argv) == 1:
            raise SystemExit('Usage: win-ci.py build|test')
        raise SystemExit('%r is not a valid action' % sys.argv[-1])


if __name__ == '__main__':
    main()
