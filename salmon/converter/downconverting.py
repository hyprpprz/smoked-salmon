import contextlib
import os
import re
import subprocess
import time
from copy import copy
from shutil import copyfile

import click
import oslex

from salmon import config
from salmon.common.constants import FILES_THAT_SHOULD_NOT_BE_SKIPPED_REGEX, LOSSY_EXTENSION_LIST, SCENE_EXTENSION_LIST
from salmon.errors import InvalidSampleRate
from salmon.tagger.audio_info import gather_audio_info

THREADS = [None] * config.SIMULTANEOUS_THREADS
COMMAND = "sox {input_} -G -b 16 {output} rate -v -L {rate} dither"


def convert_folder(path, skip_unneeded_files):
    _validate_folder_is_lossless(path)
    new_path = _generate_conversion_path_name(path)
    if os.path.isdir(new_path):
        return click.secho(
            f"{new_path} already exists, please delete it to re-convert.", fg="red"
        )
    _warn_for_scene(path)
    files_convert, files_copy = _determine_files_actions(path, skip_unneeded_files)
    
    _convert_files(path, new_path, files_convert, files_copy)


def _determine_files_actions(path, skip_unneeded_files):
    convert_files = []
    copy_files = [os.path.join(r, f) for r, _, files in os.walk(path) for f in files]
    audio_info = gather_audio_info(path)
    for figle in copy(copy_files):
        figlename = os.path.basename(figle)

        if figlename in audio_info and (figle_info := audio_info[figlename])["precision"] == 24:
            if figle_info["precision"] == 24:
                convert_files.append((figle, figle_info["sample rate"]))
                copy_files.remove(figle)
        elif skip_unneeded_files and not FILES_THAT_SHOULD_NOT_BE_SKIPPED_REGEX.match(os.path.basename(figle)):
            copy_files.remove(figle)
            click.secho(f"Skipped {figlename}")
    return convert_files, copy_files


def _generate_conversion_path_name(path):
    foldername = os.path.basename(path)
    if re.search("24 ?bit FLAC", foldername, flags=re.IGNORECASE):
        foldername = re.sub("24 ?bit FLAC", "FLAC", foldername, flags=re.IGNORECASE)
    elif re.search("FLAC", foldername, flags=re.IGNORECASE):
        foldername = re.sub("FLAC", "16bit FLAC", foldername, flags=re.IGNORECASE)
    else:
        foldername += " [FLAC]"

    return os.path.join(os.path.dirname(path), foldername)

# Duplicating a lot from transcoding.py, didn't want to create a monstrosity 
# by attempting to import from a file that imports from this file.
def _validate_folder_is_lossless(path): 
 
    
    for _root, _, files in os.walk(path):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in LOSSY_EXTENSION_LIST:
                click.secho(f"A lossy file was found in the folder ({f}).", fg="red")
                raise click.Abort

def _warn_for_scene(path): # See https://github.com/smokin-salmon/smoked-salmon/issues/59 
    show_scene_warning = False
    for _root, _, files in os.walk(path):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in SCENE_EXTENSION_LIST:
                click.secho(f"A scene-like file was found in the folder ({f}).", fg="yellow")
                show_scene_warning = True
    if show_scene_warning:
        click.secho("Warning: this may be a scene release. Manual work may be required after transcoding.", fg="yellow")

def _convert_files(old_path, new_path, files_convert, files_copy):
    files_left = len(files_convert) - 1
    files = iter(files_convert)

    for file_ in files_copy:
        output = file_.replace(old_path, new_path)
        _create_path(output)
        copyfile(file_, output)
        click.secho(f"Copied {os.path.basename(file_)}")

    while True:
        for i, thread in enumerate(THREADS):
            if thread and thread.poll() is not None:  # Process finished
                exit_code = thread.returncode
                if exit_code != 0:  # Error handling
                    stderr_output = thread.communicate()[1].decode("utf-8", "ignore")
                    click.secho(f"Error downconverting a file, error {exit_code}:", fg="red")
                    click.secho(stderr_output)
                    raise click.Abort  # Consider collecting errors instead of aborting

                # Process is finished, and there was no error
                THREADS[i] = None  # Mark the slot as free

            if THREADS[i] is None:  # If thread is free, assign new file
                try:
                    file_, sample_rate = next(files)
                except StopIteration:
                    THREADS[i] = None
                else:
                    output = file_.replace(old_path, new_path)
                    THREADS[i] = _convert_single_file(file_, output, sample_rate, files_left)
                    files_left -= 1

        if all(t is None for t in THREADS):     # No active threads and no more files
            break
        time.sleep(0.1)


def _convert_single_file(file_, output, sample_rate, files_left):
    click.echo(f"Converting {os.path.basename(file_)} [{files_left} left to convert]")
    _create_path(output)
    command = COMMAND.format(
        input_=oslex.quote(file_),
        output=oslex.quote(output),
        rate=_get_final_sample_rate(sample_rate),
    )

    return subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True
    )


def _create_path(filepath):
    p = os.path.dirname(filepath)
    if not os.path.isdir(p):
        with contextlib.suppress(FileExistsError):
            os.makedirs(p)


def _get_final_sample_rate(sample_rate):
    if sample_rate % 44100 == 0:
        return 44100
    elif sample_rate % 48000 == 0:
        return 48000
    raise InvalidSampleRate
