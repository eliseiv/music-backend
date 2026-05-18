#!/usr/bin/env bash
# Первичная настройка сервера Ubuntu 22.04+ для music-backend.
# Запускать один раз на свежей машине от root или sudo-юзера.
#
# Usage:
#   DEPLOY_USER=ubuntu REPO_URL=https://github.com/<owner>/music-backend.git ./init-server.sh
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/music-backend}"
DEPLOY_USER="${DEPLOY_USER:-${SUDO_USER:-$(id -un)}}"
REPO_URL="${REPO_URL:-}"

log() { printf '\n\033[1;36m[init-server]\033[0m %s\n' "$*"; }

# Если запущено от root — sudo не нужен. Если от обычного юзера — используем sudo.
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

require_sudo() {
    if [ -n "$SUDO" ]; then
        sudo -v
    fi
}

apt_update() {
    log "Обновляю apt и базовые утилиты"
    $SUDO bash -c "DEBIAN_FRONTEND=noninteractive apt-get update -y"
    $SUDO bash -c "DEBIAN_FRONTEND=noninteractive apt-get upgrade -y"
    $SUDO bash -c "DEBIAN_FRONTEND=noninteractive apt-get install -y \
        ca-certificates curl gnupg ufw fail2ban unattended-upgrades htop git jq"
}

setup_swap() {
    if swapon --show | grep -q '/swapfile'; then
        log "Swap уже настроен"
        return
    fi
    log "Создаю swap 2GB (диск 15GB — нужно для билдов и LLM-всплесков)"
    $SUDO fallocate -l 2G /swapfile
    $SUDO chmod 600 /swapfile
    $SUDO mkswap /swapfile
    $SUDO swapon /swapfile
    if ! grep -q '/swapfile' /etc/fstab; then
        echo '/swapfile none swap sw 0 0' | $SUDO tee -a /etc/fstab >/dev/null
    fi
    $SUDO sysctl vm.swappiness=10 || true
    echo 'vm.swappiness=10' | $SUDO tee /etc/sysctl.d/99-swappiness.conf >/dev/null
}

install_docker() {
    if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
        log "Docker и compose уже установлены"
        return
    fi
    log "Ставлю Docker Engine и compose-плагин"
    $SUDO install -m 0755 -d /etc/apt/keyrings
    if [ ! -f /etc/apt/keyrings/docker.asc ]; then
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
            $SUDO tee /etc/apt/keyrings/docker.asc >/dev/null
        $SUDO chmod a+r /etc/apt/keyrings/docker.asc
    fi
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" | \
        $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
    $SUDO apt-get update -y
    $SUDO bash -c "DEBIAN_FRONTEND=noninteractive apt-get install -y \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"

    if [ "$DEPLOY_USER" != "root" ]; then
        $SUDO usermod -aG docker "$DEPLOY_USER" || true
    fi
    $SUDO systemctl enable --now docker

    log "Настраиваю log-rotation для docker (10MB × 3)"
    $SUDO mkdir -p /etc/docker
    $SUDO tee /etc/docker/daemon.json >/dev/null <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
JSON
    $SUDO systemctl restart docker
}

setup_firewall() {
    log "Настраиваю UFW: 22/tcp, 80/tcp, 443/tcp"
    $SUDO ufw default deny incoming
    $SUDO ufw default allow outgoing
    $SUDO ufw allow 22/tcp
    $SUDO ufw allow 80/tcp
    $SUDO ufw allow 443/tcp
    $SUDO ufw --force enable
}

setup_unattended_upgrades() {
    log "Включаю автоматические security-обновления"
    $SUDO dpkg-reconfigure -f noninteractive unattended-upgrades || true
    {
        echo 'APT::Periodic::Update-Package-Lists "1";'
        echo 'APT::Periodic::Unattended-Upgrade "1";'
    } | $SUDO tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null
}

prepare_deploy_dir() {
    log "Готовлю каталог $DEPLOY_DIR"
    $SUDO mkdir -p "$DEPLOY_DIR"/{nginx/conf.d,nginx/conf.d.bootstrap,certbot/conf,certbot/www,scripts,repo}
    $SUDO chown -R "$DEPLOY_USER":"$DEPLOY_USER" "$DEPLOY_DIR"
}

clone_repo() {
    if [ -z "$REPO_URL" ]; then
        log "REPO_URL не задан — пропускаю git clone (склонируйте вручную в $DEPLOY_DIR/repo)"
        return
    fi
    if [ -d "$DEPLOY_DIR/repo/.git" ]; then
        log "Repo уже склонирован в $DEPLOY_DIR/repo"
        return
    fi
    log "Клонирую $REPO_URL в $DEPLOY_DIR/repo"
    sudo -u "$DEPLOY_USER" git clone "$REPO_URL" "$DEPLOY_DIR/repo"
}

setup_disk_cleanup_cron() {
    log "Добавляю еженедельную очистку старых docker-образов"
    CRON_LINE="0 4 * * 0 /usr/bin/docker image prune -af --filter \"until=72h\" >/var/log/docker-prune.log 2>&1"
    ( $SUDO crontab -l 2>/dev/null | grep -v 'docker image prune' ; echo "$CRON_LINE" ) | $SUDO crontab -
}

main() {
    require_sudo
    apt_update
    setup_swap
    install_docker
    setup_firewall
    setup_unattended_upgrades
    prepare_deploy_dir
    clone_repo
    setup_disk_cleanup_cron

    log "Готово."
    if [ "$DEPLOY_USER" != "root" ]; then
        log "Перелогиньтесь (или выполните 'newgrp docker'), чтобы получить группу docker без sudo."
    fi
    log "Дальше:"
    log "  1) Скопируйте содержимое каталога deploy/ в $DEPLOY_DIR (cp -r repo/deploy/. .)"
    log "  2) Замените your-domain.com на ваш домен (sed -i 's/your-domain.com/example.com/g' nginx/conf.d/*.conf nginx/conf.d.bootstrap/*.conf)"
    log "  3) Заполните .env (см. .env.production.example в репо)"
    log "  4) Запустите ./scripts/init-letsencrypt.sh"
    log "  5) Запустите ./scripts/deploy.sh для первого деплоя"
}

main "$@"
