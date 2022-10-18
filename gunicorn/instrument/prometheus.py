# -*- coding: utf-8 -
#
# This file is part of gunicorn released under the MIT license.
# See the NOTICE for more information.

"Bare-bones implementation of prometheus's protocol, client-side"

import logging
from os import getenv, getpid

from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import (
    set_meter_provider,
    get_meter_provider,
)
from opentelemetry.sdk.metrics import MeterProvider, Histogram, Counter
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)

from gunicorn.glogging import Logger


class Prometheus(Logger):
    """prometheus-based instrumentation, that passes as a logger
    """
    def __init__(self, cfg):
        """host, port: prometheus server
        """
        Logger.__init__(self, cfg)

        temporality_cumulative = {
            Counter: AggregationTemporality.CUMULATIVE,
            Histogram: AggregationTemporality.CUMULATIVE,
        }

        host, port = cfg.otlp_endpoint
        endpoint = f"{host}:{port}"

        exporter = OTLPMetricExporter(
            endpoint=endpoint, insecure=True, preferred_temporality=temporality_cumulative
        )
        if getenv("OTEL_METRICS_EXPORTER", "otlp") == "console":
            exporter = ConsoleMetricExporter()
        reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=5000,
        )
        provider = MeterProvider(metric_readers=[reader])
        set_meter_provider(provider)

        meter = get_meter_provider().get_meter("gunicorn")
        self.log_counter = meter.create_counter("gunicorn.log")
        self.request_histogram = meter.create_histogram("gunicorn.request", unit="ms")

        logging.getLogger("gunicorn.access").addHandler(UvicornHandler(self.request_histogram))

    # Log errors and warnings
    def critical(self, msg, *args, **kwargs):
        Logger.critical(self, msg, *args, **kwargs)
        self.log_counter.add(1, {"type": "critical"})

    def error(self, msg, *args, **kwargs):
        Logger.error(self, msg, *args, **kwargs)
        self.log_counter.add(1, {"type": "error"})

    def warning(self, msg, *args, **kwargs):
        Logger.warning(self, msg, *args, **kwargs)
        self.log_counter.add(1, {"type": "warning"})

    def exception(self, msg, *args, **kwargs):
        Logger.exception(self, msg, *args, **kwargs)
        self.log_counter.add(1, {"type": "exception"})

    # Special treatment for info, the most common log level
    def info(self, msg, *args, **kwargs):
        self.log(logging.INFO, msg, *args, **kwargs)

    # skip the run-of-the-mill logs
    def debug(self, msg, *args, **kwargs):
        self.log(logging.DEBUG, msg, *args, **kwargs)

    def log(self, lvl, msg, *args, **kwargs):
        """Log a given statistic if metric, value and type are present
        """
        Logger.log(self, lvl, msg, *args, **kwargs)


class UvicornHandler(logging.Handler):

    def __init__(self, histogram):
        super().__init__()
        self.histogram = histogram

    def emit(self, record):
        if record.name != "uvicorn.access":
            return

        status = record.args["s"]
        request_time_microseconds = record.args["D"]

        duration_in_ms = float(request_time_microseconds) / 10 ** 3

        self.histogram.record(duration_in_ms, {"status": status, "worker_pid": getpid()})