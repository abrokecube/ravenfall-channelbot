import asyncio
import subprocess
import os
import logging

# Configure logger for this module
logger = logging.getLogger(__name__)

async def runshell(cmd) -> str | None:
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    stdout, stderr = await proc.communicate()
    out_text = None
    logger.debug(f'Command {cmd!r} exited with code {proc.returncode}')
    if stdout:
        stdout_text = stdout.decode()
        logger.debug(f'Command stdout: {stdout_text.replace("\n", "\\n")}')
        out_text = stdout_text
    if stderr:
        logger.error(f'Command stderr: {stderr.decode().replace("\n", "\\n")}')
    return proc.returncode, out_text

def runshell_detached(cmd):
    DETACHED_PROCESS = 0x00000008
    subprocess.Popen(
        cmd,
        shell=True,
        creationflags=DETACHED_PROCESS
    )

async def restart_process(box_name, process_name, startup_command: str):
    shellcmd = (
        f"\"{os.getenv('SANDBOXIE_START_PATH')}\" /box:{box_name} /silent /wait "
        f"taskkill /f /im {process_name}"
    )
    code, text = await runshell(shellcmd)
    await asyncio.sleep(5)
    shellcmd = (
        f"\"{os.getenv('SANDBOXIE_START_PATH')}\" /box:{box_name} /silent /wait "
        f"cmd /c \"{startup_command.replace("\"", "\\\"")}\""
    )
    code, text = await runshell(shellcmd)
    return code, text
