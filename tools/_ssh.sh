# Общие параметры ssh/scp/rsync с ПК владельца (кириллица в HOME: `-i` и `UserKnownHostsFile` обязательны, AGENTS.md «Машины»).
# Подключение: . "$(dirname "${BASH_SOURCE[0]}")/_ssh.sh" (из tools/p42 — `/../_ssh.sh`); свои параметры скрипт добавляет после:
#   KEY=("${SSH_ID[@]}" -o ConnectTimeout=15 -o BatchMode=yes)
SSH_ID=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts)
