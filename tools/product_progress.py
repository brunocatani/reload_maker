"""Progress for explicitly requested CLI jobs, consumed by the existing GUI job."""
from contextlib import contextmanager
import json
import os
import time

_measurements = []


def progress(stage, **values):
    if os.environ.get('RELOAD_MAKER_JOB_PROGRESS') == '1':
        print(json.dumps(dict(event='progress', stage=stage, **values)), flush=True)


@contextmanager
def measure(stage, **values):
    started = time.perf_counter()
    progress(stage, **values)
    try:
        yield
    finally:
        result = dict(stage=stage, milliseconds=round((time.perf_counter()-started)*1000, 3), **values)
        _measurements.append(result)
        progress(stage, finished=True, **{k:v for k,v in result.items() if k != 'stage'})


def measurements():
    return list(_measurements)
