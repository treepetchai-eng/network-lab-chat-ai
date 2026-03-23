#!/usr/bin/env python3
"""Check syslog-ng config on remote server."""
import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("100.93.135.57", username="treepetch", password="gilardino01")

cmds = [
    "ls /etc/syslog-ng/conf.d/",
    "cat /etc/syslog-ng/conf.d/20-aiops-http.conf 2>/dev/null || echo 'FILE NOT FOUND'",
    "grep -r 'http\\|destination.*d_' /etc/syslog-ng/conf.d/ 2>/dev/null | head -20",
    "systemctl is-active syslog-ng",
]
for cmd in cmds:
    print(f"\n=== {cmd} ===")
    _, stdout, stderr = ssh.exec_command(cmd)
    print(stdout.read().decode().strip())
    err = stderr.read().decode().strip()
    if err:
        print(f"STDERR: {err}")
ssh.close()
print("\nDone!")
