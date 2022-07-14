#!/bin/sh

set -e

# activate our virtual environment here
. /opt/pysetup/.venv/bin/activate

# setup an empty directory for Prometheus metrics
if [ ${PROMETHEUS_MULTIPROC:-0} -eq 1 ]; then
    prometheus_multiproc_dir=`mktemp -td prometheus.XXXXXXXXXX` || exit 1
    export prometheus_multiproc_dir
fi

# Evaluating passed command:
exec "$@"

if [ -z ${prometheus_multiproc_dir+x} ]; then
    rm -rf ${prometheus_multiproc_dir}
fi
