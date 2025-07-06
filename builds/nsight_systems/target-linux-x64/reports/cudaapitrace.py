#!/usr/bin/env python

import nsysstats

class CUDAAPITrace(nsysstats.StatsReport):

    display_name = 'CUDA API Trace'
    usage = f"""{{SCRIPT}} -- CUDA API Trace

    No arguments.

    Output: All time values default to nanoseconds
        Start : Timestamp when API call was made
        Duration : Length of API calls
        Name : API function name
        Result : Return value of API call
        CorrID : Correlation used to map to other CUDA calls
        Pid : Process ID that made the call
        Tid : Tread ID that made the call
        T-Pri : Run priority of call thread
        Thread Name : Name of thread that called API function

    This report provides a trace record of CUDA API function calls and
    their execution times.
"""

    query = """
SELECT
    api.start AS "Start:ts_ns",
    api.end - api.start AS "Duration:dur_ns",
    CASE substr(nstr.value, -6, 2)
        WHEN '_v'THEN substr(nstr.value, 1, length(nstr.value)-6)
        ELSE nstr.value
    END AS "Name",
    api.returnValue AS "Result",
    api.correlationId AS "CorrID",
    -- (api.globalTid >> 40) & 0xFF AS "HWid",
    -- (api.globalTid >> 32) & 0xFF AS "VMid",
    (api.globalTid >> 24) & 0xFFFFFF AS "Pid",
    (api.globalTid      ) & 0xFFFFFF AS "Tid",
    tname.priority AS "T-Pri",
    tstr.value AS "Thread Name"
FROM
    CUPTI_ACTIVITY_KIND_RUNTIME AS api
LEFT OUTER JOIN
    StringIds AS nstr
    ON nstr.id == api.nameId
LEFT OUTER JOIN
    ThreadNames AS tname
    ON tname.globalTid == api.globalTid
LEFT OUTER JOIN
    StringIds AS tstr
    ON tstr.id == tname.nameId
ORDER BY 1
;
"""

    table_checks = {
        'CUPTI_ACTIVITY_KIND_RUNTIME':
            '{DBFILE} does not contain CUDA trace data.'
    }

if __name__ == "__main__":
    CUDAAPITrace.Main()
