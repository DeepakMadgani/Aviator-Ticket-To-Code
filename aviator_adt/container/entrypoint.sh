#!/usr/bin/env bash
set -euo pipefail

# Accept exactly one runtime mode argument: app, worker, beat, or migration.
readonly MODE="${1:-}"

usage() {
	echo "Usage: $0 {app|worker|beat|embedding-worker|migration|summary-backfill|mcp-server}" >&2
}

install_path_items() {
	local install_root="$1"
	shift

	local -a items=()
	while IFS= read -r item; do
		items+=("$item")
	done < <(find "$install_root" "$@" | sort)

	if [[ ${#items[@]} -eq 0 ]]; then
		# Nothing to install from this path.
		return 0
	fi

	echo "Installing ${#items[@]} item(s) from: $install_root"
	# Install all artifacts/plugins from the path in one call without resolving transitive deps.
	uv pip install --no-deps "${items[@]}"
}

# Validate mode early so we fail fast on bad container args.
if [[ "$MODE" != "app" && "$MODE" != "worker" && "$MODE" != "beat" && "$MODE" != "mcp-server" && "$MODE" != "embedding-worker" && "$MODE" != "migration" && "$MODE" != "summary-backfill" ]]; then
	usage
	exit 1
fi

# Install dependency artifacts first so plugin installs can rely on them.
if [[ -d "/plugins/deps" ]]; then
    echo "Installing dependency artifacts from /plugins/deps"
	install_path_items "/plugins/deps" -mindepth 1 -maxdepth 1
fi

# Install plugin packages, skipping the dedicated deps directory.
if [[ -d "/plugins" ]]; then
    echo "Installing plugin packages from /plugins"
	install_path_items "/plugins" -mindepth 1 -maxdepth 1 -not -name deps
fi

# Start only the requested runtime role.
# Auto-detect worker queue index from StatefulSet hostname if not explicitly set.
# In a StatefulSet, hostname is <name>-<ordinal>, so we extract the trailing number.
if [[ ("$MODE" == "worker" || "$MODE" == "embedding-worker") && -z "${WORKER_QUEUE_INDEX:-}" && "${BROKER_CONSISTENT_HASH_ENABLED:-false}" == "true" ]]; then
	_candidate="${HOSTNAME##*-}"
	if [[ "$_candidate" =~ ^[0-9]+$ ]]; then
		export WORKER_QUEUE_INDEX="$_candidate"
		echo "Auto-detected WORKER_QUEUE_INDEX=$WORKER_QUEUE_INDEX from hostname $HOSTNAME"
	else
		echo "WARNING: Could not derive numeric WORKER_QUEUE_INDEX from hostname '$HOSTNAME'; worker will consume all queues"
	fi
fi

case "$MODE" in
app)
	echo "Starting application..."
	exec aviator
	;;
worker|embedding-worker)
	echo "Starting worker..."
	exec celery -A aviator.celery worker -l info -P solo ${WORKER_QUEUES:+-Q "$WORKER_QUEUES"} --hostname="aviator-worker@%h" --without-mingle --without-gossip --without-heartbeat
	;;
beat)
    echo "Starting beat..."
	exec celery -A aviator.celery beat -l info -S aviator.services.local_beat_scheduler.LocalBeatScheduler --schedule /tmp/celerybeat-schedule
	;;
mcp-server)
    echo "Starting MCP server..."
    exec python -m aviator.mcp.server.mcp_server
    ;;
migration)
	echo "Starting migration producer..."
	exec python -m migration.run_producer
	;;
summary-backfill)
	echo "Starting summary backfill producer..."
	exec python -m migration.run_summary_producer
	;;
esac