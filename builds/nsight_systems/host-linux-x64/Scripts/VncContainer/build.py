#! /usr/bin/env python3

# SPDX-FileCopyrightText: Copyright (c) 2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved. 
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction, 
# disclosure or distribution of this material and related documentation 
# without an express license agreement from NVIDIA CORPORATION or 
# its affiliates is strictly prohibited.

import argparse
import atexit
import getpass
import grp
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import traceback
import uuid
from distutils import dir_util
from glob import glob
from multiprocessing import Lock, Pool, cpu_count
from pathlib import Path

CONTAINER_NAME = "nsys-ui-vnc"
CONTAINER_VERSION = "1.0"

TEMP_FS_OBJECTS = []

PASSWORD_PROMPT_TIMEOUT = 120  # seconds


def atexit_func():
    """Cleanup on exit"""
    for path in TEMP_FS_OBJECTS:
        try:
            if os.path.isfile(path) or os.path.islink(path):
                os.remove(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)
        except:
            pass


# to sync std output
PRINT_LOCK = Lock()
print_old = print


def print(*args, **kwargs):
    """Synchronized print"""
    with PRINT_LOCK:
        print_old(*args, **kwargs)


def print_error(*args, **kwargs):
    """Error print"""
    CRED_PREFIX = "\033[91m"
    CRED_SUFFIX = "\033[0m"
    print(CRED_PREFIX + "[ERROR]:", *args, CRED_SUFFIX, **kwargs)


def use_pigz():
    return bool(shutil.which("pigz"))


class WeakPasswordException(Exception):
    pass


class UnrecoverableChildProcessException(Exception):
    pass


def compress_with_pigz(output_filename, source_dir):
    """Create tar.gz archive using pigz (multithreaded)

    Args:
        output_filename ([str]): The full path to the archieve
        source_dir ([str]): The directory to be archieved
    """

    source_path = Path(source_dir)
    print("Archieving {} to {}".format(source_dir, output_filename))
    tar_cmd = [
        "tar",
        "--warning=no-file-ignored",
        "--warning=no-file-changed",
        "--warning=no-file-removed",
        "--use-compress-program={} -3  -p {}".format(shutil.which("pigz"), cpu_count()),
        "-cf",
        output_filename,
        "-C",
        str(source_path.parent.absolute()),
        source_path.name,
    ]

    p = subprocess.Popen(tar_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out_, err_ = p.communicate()
    exit_code = p.wait()
    # It is OK for tar to return 1 (http://www.gnu.org/software/tar/manual/html_section/Synopsis.html)
    if exit_code not in [0, 1]:
        print_error(
            "Binaries copying failed with exit code {}: {}\n{}".format(
                exit_code, err_.decode("utf-8"), out_.decode("utf-8")
            )
        )
        raise UnrecoverableChildProcessException()


def compress_with_tarfile(output_filename, source_dir):
    """Create tar.gz archive using tarfile

    Args:
        output_filename ([str]): The full path to the archieve
        source_dir ([str]): The directory to be archieved
    """
    with tarfile.open(output_filename, "w:gz", compresslevel=3) as tar:
        tar.add(source_dir, arcname=os.path.basename(source_dir))
    return output_filename


def compress_folder(output_filename, source_dir):
    """Create tar.gz archive

    Args:
        output_filename ([str]): The full path to the archieve
        source_dir ([str]): The directory to be archieved

    Returns:
        [str]: output_filename
    """
    if use_pigz():
        try:
            compress_with_pigz(output_filename, source_dir)
            return output_filename
        except Exception:
            print(traceback.format_exc())
            print("Failed to compress with pigz... Switching to the default compression")

    compress_with_tarfile(output_filename, source_dir)
    return output_filename


def check_directories(host_directory, target_directories):
    """Check whether passsed directories are correct Nsys binaries

    Args:
        host_directory (str, optional): The directory with Nsys host binaries.
        target_directory (str, optional): The directory with Nsys target binaries.

    """
    nsys_ui_dir = os.path.join(host_directory, "nsys-ui")
    host_directory_path = Path(nsys_ui_dir)
    if not host_directory_path.is_file():
        error_text = "{} not found. Make sure that you are using Nsys host binaries with GUI support".format(
            nsys_ui_dir
        )
        print_error(error_text)
        raise FileNotFoundError(error_text)


def prepare_binaries(dockerfile_dir, host_directory=None, target_directories=None):
    """Prepare archieves for copying to the docker container
        default Dockerfile directory is host-.../Scripts/VncContainer

    Args:
        host_directory (str, optional): The directory with Nsys host binaries. Defaults to
            "../../.".
        target_directory (str, optional): The directory with Nsys target binaries. Defaults to
            "../../../target-*".
    """
    print("Preparing Nsight Systems binaries...")
    if not host_directory:
        script_dir = os.path.realpath(__file__)
        host_directory = Path(script_dir).parent.parent.parent.absolute()
    host_folder_name = Path(host_directory).name

    if not target_directories:
        target_directories = glob(os.path.join(Path(host_directory).parent.absolute(), "target-*", ""))
    target_folder_names = [Path(td).name for td in target_directories]

    check_directories(host_directory, target_directories)

    archieves = [(host_folder_name, host_directory)] + list(zip(target_folder_names, target_directories))
    archive_create_args = []
    for arch_name, source_dir in archieves:
        if not Path(source_dir).is_dir():
            print_error("Binaries directory ({}) does not exist".format(source_dir))
            raise FileNotFoundError(source_dir)

        full_arch_path = os.path.join(dockerfile_dir, arch_name) + ".tar.gz"
        TEMP_FS_OBJECTS.append(full_arch_path)
        archive_create_args.append([full_arch_path, source_dir])

    with Pool(processes=2) as pool:
        pool.starmap(compress_folder, archive_create_args)

    return (host_folder_name, target_folder_names)


def parse_geometry_arg(geometry):
    """Check and parse value of "--geometry" (with the format WidthxHeight) argument

    Args:
        geometry ([str]): Passed to command line "--geometry" (with the format WidthxHeight) argument

    Returns:
        [tuple]: width and height
    """
    if not geometry:
        return None

    geometry_arr = geometry.split("x")
    if geometry_arr and len(geometry_arr) == 2:
        try:
            width = int(geometry_arr[0])
            height = int(geometry_arr[1])
            return (width, height)
        except ValueError:
            pass
    print_error("Wrong format of geometry argument. Should be WidthxHeight (for ex. 1200x800)")
    return None


def verify_password_complexity(password):
    """Verify passwords complexity requirements for password

    Args:
        password ([str]): Password to verify complexity requirements

    Returns:
        [boolean]: True if password meets complexity requirements
    """
    if not password or len(password) < 6:
        print_error("VNC password needs to be at least 6 characters long")
        return False
    return True


def get_vnc_password(vnc_password_arg):
    """Get VNC password from the passed argument or from stdin

    Args:
        vnc_password_arg ([str]): Password from the passed argument

    Returns:
        [str]: VNC password
    """
    if vnc_password_arg:
        if not verify_password_complexity(vnc_password_arg):
            raise WeakPasswordException("Password needs to be at least 6 characters long")
        return vnc_password_arg

    # default vnc password can be empty
    if vnc_password_arg is None:
        return ""

    if hasattr(sys.stdin, "isatty") and not sys.stdin.isatty():
        raise IOError("Vnc-password argument is not specified and current terminal is not interactive.")

    def _interuptPassword(signum, frame):
        print_error("\nPassword promt timeout")
        raise TimeoutError("Password promt timeout")

    previous = signal.signal(signal.SIGALRM, _interuptPassword)
    try:
        signal.alarm(PASSWORD_PROMPT_TIMEOUT)
        vnc_password = getpass.getpass("Enter required VNC password (at least 6 characters): ")
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)

    if not verify_password_complexity(vnc_password):
        return get_vnc_password("")
    return vnc_password


def docker_requires_sudo():
    """Check if docker can be run without sudo."""
    try:
        group = grp.getgrnam("docker")
    except KeyError:
        return True

    return group.gr_gid not in os.getgroups()


def configure_copy_target_folders(dockerfile_data, target_folder_names):
    """Configure Dockerfile script with dynamic target folder names

    Args:
        dockerfile_data (str): Dockerfile script
        target_folder_names (list):  Nsys targets folder names

    Returns:
        str:  Configured Dockerfile script
    """
    copy_target_folders_command = ""
    for target_folder_name in target_folder_names:
        copy_target_folders_command += "ADD " + target_folder_name + ".tar.gz ./\n"
    dockerfile_data = dockerfile_data.replace("# $$TARGET_FOLDERS_COPY$$ #", copy_target_folders_command)

    chown_target_folders_command = 'RUN chown -R $NSYS_USER "' + '" "'.join(target_folder_names) + '"'
    dockerfile_data = dockerfile_data.replace("# $$TARGET_FOLDERS_CHOWN$$ #", chown_target_folders_command)

    return dockerfile_data


def configure_dockerfile(dockerfile_dir, target_folder_names):
    """Configure Dockerfile script with dynamic variables

    Args:
        dockerfile_dir (str): Direcrory with the original Dockerfile script
        target_folder_names (list): Nsys targets folder names

    Returns:
        str: Path to the configured Dockerfile
    """
    with open(os.path.join(dockerfile_dir, "Dockerfile"), "r") as origin_file:
        dockerfile_data = origin_file.read()

    dockerfile_data = configure_copy_target_folders(dockerfile_data, target_folder_names)

    configured_dockerfile_path = os.path.join(dockerfile_dir, "Dockerfile.configured")
    with open(configured_dockerfile_path, "w") as result_file:
        result_file.write(dockerfile_data)

    return configured_dockerfile_path


def build_image(
    dockerfile_dir,
    host_folder_name,
    target_folder_names,
    vnc_password,
    additional_build_arguments,
    tigervnc,
    novnc,
    rdp,
    geometry,
):
    """Build docker container image

    Args:
        additional_build_arguments ([str]): Additional arguments, which will be passed to the "docker build" command
        tigervnc ([boolean]): Use TigerVNC instead of x11vnc
        novnc ([boolean]): Install noVNC in the docker container for HTTP access
        rdp ([boolean]): Install xRDP in the docker for RDP access
    """
    docker_build_args = ["sudo", "--preserve-env=VNC_DEFAULT_PASSWORD"] if docker_requires_sudo() else []
    docker_build_args += ["env", "DOCKER_BUILDKIT=1", "docker", "build"]

    build_env = os.environ.copy()

    vnc_default_password = get_vnc_password(vnc_password)
    if vnc_default_password is not None:
        build_env["VNC_DEFAULT_PASSWORD"] = vnc_default_password

    docker_build_args.extend(["--secret", "id=vnc_password_arg,env=VNC_DEFAULT_PASSWORD"])
    docker_build_args.extend(
        ["--build-arg", "VNC_DEFAULT_PASSWORD_SECRET_SEED={}".format(uuid.uuid4().hex)]
    )

    docker_build_args.extend(["--build-arg", "HOST_FOLDER_NAME_ARG={}".format(host_folder_name)])
    if tigervnc:
        docker_build_args.extend(["--build-arg", "WITH_TIGERVNC_ARG=yes"])
    if novnc:
        docker_build_args.extend(["--build-arg", "WITH_NOVNC_ARG=yes"])
    if rdp:
        docker_build_args.extend(["--build-arg", "WITH_RDP_ARG=yes"])

    geometry = parse_geometry_arg(geometry)
    if geometry:
        docker_build_args.extend(["--build-arg", "WINDOW_WIDTH_ARG={}".format(geometry[0])])
        docker_build_args.extend(["--build-arg", "WINDOW_HEIGHT_ARG={}".format(geometry[1])])

    if additional_build_arguments:
        docker_build_args.extend(shlex.split(additional_build_arguments))

    docker_build_args.extend(["-t", "{}:{}".format(CONTAINER_NAME, CONTAINER_VERSION)])

    configured_dockerfile_path = configure_dockerfile(dockerfile_dir, target_folder_names)
    docker_build_args.extend(["-f", configured_dockerfile_path])
    TEMP_FS_OBJECTS.append(configured_dockerfile_path)

    docker_build_args.append(dockerfile_dir)

    try:
        print("Building container:\n" + str(docker_build_args))
        subprocess.check_call(docker_build_args, env=build_env)
        print("Docker container is built: {}:{}".format(CONTAINER_NAME, CONTAINER_VERSION))
    except subprocess.CalledProcessError as ex:
        print_error("A fatal error occurred during container build. Stopping")
        raise


def create_image_in_directory(dockerfile_dir, args):
    """Create image with given aruments in

    Args:
        args ([argparse.Namespace]): Parsed command line arguments
    """
    host_folder_name, target_folder_names = prepare_binaries(
        dockerfile_dir, args.nsys_host_directory, args.nsys_target_directory
    )
    build_image(
        dockerfile_dir,
        host_folder_name,
        target_folder_names,
        args.vnc_password,
        args.additional_build_arguments,
        args.tigervnc,
        args.http,
        args.rdp,
        args.geometry,
    )


def create_image(args):
    """ "Create image with given arguments. If the build-directory argument is specified - use this
    directory. If no - checks access to the script directory. If the script directory is writable,
    use this directory, else use the temp directory.

    Args:
        args ([argparse.Namespace]): Parsed command line arguments
    """
    script_dir = os.path.dirname(os.path.realpath(__file__))
    if args.build_directory:
        build_dir = args.build_directory
        permission_error = (
            "build-directory ({}) is not writable. Please, specify the directory "
            "with write permissions for the current user"
        ).format(args.build_directory)

        if not os.path.exists(build_dir):
            try:
                TEMP_FS_OBJECTS.append(build_dir)
                Path(build_dir).mkdir(parents=True, exist_ok=True)
            except PermissionError:
                print_error(permission_error)
                raise

        if not os.access(build_dir, os.W_OK):
            print_error(permission_error)
            raise PermissionError(permission_error)

        tmp_container_dir = os.path.join(build_dir, "container")
        TEMP_FS_OBJECTS.append(tmp_container_dir)
        dir_util.copy_tree(script_dir, tmp_container_dir)
        create_image_in_directory(tmp_container_dir, args)
    elif os.access(script_dir, os.W_OK):
        create_image_in_directory(script_dir, args)
    else:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_container_dir = os.path.join(tmp_dir, "container")
            TEMP_FS_OBJECTS.append(tmp_container_dir)
            print("Current direcory is not writable, copying and building in {}".format(tmp_container_dir))
            dir_util.copy_tree(script_dir, tmp_container_dir)
            create_image_in_directory(tmp_container_dir, args)


if __name__ == "__main__":
    atexit.register(atexit_func)
    parser = argparse.ArgumentParser(description="Build container with Nsys UI and VNC server.")
    parser.add_argument(
        "-aba",
        "--additional-build-arguments",
        help="Additional arguments which will be passed to a docker build command",
    )
    parser.add_argument("-hd", "--nsys-host-directory", help="The directory with Nsys host binaries (with GUI)")
    parser.add_argument(
        "-td",
        "--nsys-target-directory",
        action="append",
        help="Directory with Nsys target binaries (can be specified multiple times)",
    )
    parser.add_argument("--vnc-password", nargs="?", const="", help="Password for VNC access (at least 6 characters)")
    parser.add_argument("--tigervnc", action="store_true", help="Use TigerVNC instead of x11vnc")
    parser.add_argument("--http", action="store_true", help="Install noVNC in the docker container for HTTP access")
    parser.add_argument("--rdp", action="store_true", help="Install xRDP in the docker for RDP access")
    parser.add_argument(
        "--geometry", help="Original VNC server resolution in the format WidthxHeight (default 1920x1080)"
    )
    parser.add_argument(
        "--build-directory",
        help=(
            "The directory to save temporary files (with the write access for the current user). "
            "By default, script or tmp directory will be used."
        ),
    )

    args = parser.parse_args()
    create_image(args)
