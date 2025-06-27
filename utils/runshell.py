import asyncio
import subprocess
import os

async def runshell(cmd) -> str | None:
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)

    stdout, stderr = await proc.communicate()
    out_text = None
    print(f'[{cmd!r} exited with {proc.returncode}]')
    if stdout:
        stdout_text = stdout.decode()
        print(f'[stdout]\n{stdout_text}')
        out_text = stdout_text
    if stderr:
        print(f'[stderr]\n{stderr.decode()}')
    return out_text

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
    await runshell(shellcmd)
    shellcmd = (
        f"\"{os.getenv('SANDBOXIE_START_PATH')}\" /box:{box_name} /silent /wait "
        f"cmd /c \"{startup_command.replace("\"", "\\\"")}\""
    )
    await runshell(shellcmd)
