#!/usr/bin/env python
# vim:fileencoding=utf-8

from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import os
import re
import subprocess
import sys

try:
    import _winreg as winreg
except ImportError:
    import winreg

is64bit = os.environ.get('PLATFORM') != 'x86'


def vcvars():  # {{{
    RegOpenKeyEx = winreg.OpenKeyEx
    RegEnumValue = winreg.EnumValue
    RegError = winreg.error

    HKEYS = (winreg.HKEY_USERS, winreg.HKEY_CURRENT_USER,
             winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CLASSES_ROOT)
    VS_BASE = r"Software\Wow6432Node\Microsoft\VisualStudio\%0.1f"

    def get_reg_value(path, key):
        for base in HKEYS:
            d = read_values(base, path)
            if d and key in d:
                return d[key]
        raise KeyError(key)

    def convert_mbcs(s):
        dec = getattr(s, "decode", None)
        if dec is not None:
            try:
                s = dec("mbcs")
            except UnicodeError:
                pass
        return s

    def read_values(base, key):
        """Return dict of registry keys and values.
        All names are converted to lowercase.
        """
        try:
            handle = RegOpenKeyEx(base, key)
        except RegError:
            return None
        d = {}
        i = 0
        while True:
            try:
                name, value, type = RegEnumValue(handle, i)
            except RegError:
                break
            name = name.lower()
            d[convert_mbcs(name)] = convert_mbcs(value)
            i += 1
        return d

    def find_vcvarsall(version=14.0):
        vsbase = VS_BASE % version
        try:
            productdir = get_reg_value(r"%s\Setup\VC" % vsbase, "productdir")
        except KeyError:
            raise SystemExit("Unable to find Visual Studio product directory"
                             " in the registry")

        if not productdir:
            raise SystemExit("No productdir found")
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

    def query_process(cmd):
        if is64bit and 'PROGRAMFILES(x86)' not in os.environ:
            os.environ['PROGRAMFILES(x86)'] = os.environ[
                'PROGRAMFILES'] + ' (x86)'
        result = {}
        popen = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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

    def query_vcvarsall():
        plat = 'amd64' if is64bit else 'x86'
        vcvarsall = find_vcvarsall()
        env = query_process('"%s" %s & set' % (vcvarsall, plat))

        def g(k):
            try:
                return env[k]
            except KeyError:
                return env[k.lower()]

        # We have to insert the correct path to MSBuild.exe so that the one
        # from the .net frameworks is not used.
        paths = g('PATH').split(os.pathsep)
        for i, p in enumerate(tuple(paths)):
            if os.path.exists(os.path.join(p, 'MSBuild.exe')):
                if '.net' in p.lower():
                    paths.insert(i,
                                 r'C:\Program Files (x86)\MSBuild\14.0\bin' +
                                 (r'\amd64' if is64bit else ''))
                    env["PATH"] = os.pathsep.join(paths)
                break

        return {
            k: g(k)
            for k in 'PATH LIB INCLUDE LIBPATH WINDOWSSDKDIR VS140COMNTOOLS'
            ' UCRTVERSION UNIVERSALCRTSDKDIR'.split()
        }

    return query_vcvarsall()


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
    env.update(vcvars())
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
    replace_in_file('PCbuild\\build.bat', '%1', '"/p:PlatformToolset=v140"')
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
