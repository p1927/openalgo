#!/bin/bash
# Shared VALID_BROKERS list sourced from .sample.env (single authority).

get_valid_brokers_csv() {
    local repo_root="${1:-}"
    if [ -z "$repo_root" ]; then
        repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    fi
    local sample="${repo_root}/.sample.env"
    if [ ! -f "$sample" ]; then
        echo ""
        return 1
    fi
    grep -E '^VALID_BROKERS\s*=' "$sample" | head -1 | sed -E "s/^VALID_BROKERS\s*=\s*['\"]?([^'\"]*)['\"]?.*/\1/" | tr -d ' '
}

validate_broker_name() {
    local broker="$1"
    local repo_root="$2"
    local valid_brokers
    valid_brokers="$(get_valid_brokers_csv "$repo_root")"
    if [ -z "$valid_brokers" ]; then
        return 1
    fi
    [[ ",$valid_brokers," == *",$broker,"* ]]
}
