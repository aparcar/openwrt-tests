#!/bin/bash
set -e

case "${LABGRID_MODE}" in
    coordinator)
        echo "Starting labgrid-coordinator..."
        exec labgrid-coordinator \
            --listen "${LABGRID_COORDINATOR_LISTEN:-::}" \
            ${LABGRID_COORDINATOR_ARGS:-}
        ;;
    exporter)
        if [ ! -f "${LABGRID_CONFIG}" ]; then
            echo "ERROR: Exporter config not found at ${LABGRID_CONFIG}"
            exit 1
        fi
        echo "Starting labgrid-exporter..."
        exec labgrid-exporter \
            --config "${LABGRID_CONFIG}" \
            --name "${LABGRID_EXPORTER_NAME:-$(hostname)}" \
            ${LABGRID_EXPORTER_ARGS:-}
        ;;
    *)
        echo "Unknown mode: ${LABGRID_MODE}"
        echo "Use LABGRID_MODE=coordinator or LABGRID_MODE=exporter"
        exit 1
        ;;
esac
