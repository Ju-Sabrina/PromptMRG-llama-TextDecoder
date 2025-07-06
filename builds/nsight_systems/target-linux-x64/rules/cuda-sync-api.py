#!/usr/bin/env python

import nsysstats

class SyncAPI(nsysstats.ExpertSystemsReport):

    display_name = "CUDA Synchronization APIs"
    usage = f"""{{SCRIPT}}[:options...] -- Synchronous APIs

    Options:
        rows=<limit> - Maximum number of rows returned by the query.
            Default is {{ROW_LIMIT}}.

        start=<time> - Start time used for filtering in nanoseconds.

        end=<time> - End time used for filtering in nanoseconds.

        nvtx=<range[@domain]> - NVTX range text and domain used for filtering.
            Do not specify the domain for ranges in the default domain.
            Note that only the first matching record will be considered. If
            this option is used along with the 'start' and/or 'end' options,
            the explicit start/end times will override the NVTX range times.

    Output: All time values default to nanoseconds
        Duration : Duration of the synchronization event
        Start : Start time of the synchronization event
        PID : Process identifier
        TID : Thread identifier
        API Name : Runtime API function name

    This rule identifies the following synchronization APIs that block the
    host until the issued CUDA calls are complete:
    - cudaDeviceSynchronize()
    - cudaStreamSynchronize()
"""

    message_advice = ("The following are synchronization APIs that block the"
        " host until all issued CUDA calls are complete.\n\n"
        "Suggestions:\n"
        "   1. Avoid excessive use of synchronization.\n"
        "   2. Use asynchronous CUDA event calls, such as cudaStreamWaitEvent()"
        " and cudaEventSynchronize(), to prevent host synchronization.")

    message_noresult = ("There were no problems detected related to"
        " synchronization APIs.")

    query_sync_api = """
    WITH
        sid AS (
            SELECT
                *
            FROM
                StringIds
            WHERE
                   value like 'cudaDeviceSynchronize%'
                OR value like 'cudaStreamSynchronize%'
        )
    SELECT
        runtime.end - runtime.start AS "Duration:dur_ns",
        runtime.start AS "Start:ts_ns",
        (runtime.globalTid >> 24) & 0x00FFFFFF AS "PID",
        runtime.globalTid & 0xFFFFFF AS "TID",
        sid.value AS "API Name",
        runtime.globalTid AS "_Global ID",
        'cuda' AS "_API"
    FROM
        CUPTI_ACTIVITY_KIND_RUNTIME AS runtime
    JOIN
        sid
        ON sid.id == runtime.nameId
    ORDER BY
        1 DESC
    LIMIT {ROW_LIMIT}
"""

    table_checks = {
        'CUPTI_ACTIVITY_KIND_RUNTIME':
            "{DBFILE} could not be analyzed because it does not contain the required CUDA data."
            " Does the application use CUDA runtime APIs?"
    }

    def setup(self):
        err = super().setup()
        if err != None:
            return err

        self.query = self.query_sync_api.format(
            ROW_LIMIT = self._row_limit)

if __name__ == "__main__":
    SyncAPI.Main()
