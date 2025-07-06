#!/usr/bin/env python

import nsysstats

class SyncMemset(nsysstats.ExpertSystemsReport):

    display_name = "CUDA Synchronous Memset"
    usage = f"""{{SCRIPT}}[:options...] -- Synchronous Memset

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
        Duration : Duration of memset on GPU
        Start : Start time of memset on GPU
        Memory Kind : Type of memory being set
        Bytes : Number of bytes set
        PID : Process identifier
        Device ID : GPU device identifier
        Context ID : Context identifier
        Stream ID : Stream identifier
        API Name : Runtime API function name

    This rule identifies synchronous memset operations with pinned host memory
    or Unified Memory region.
"""

    message_advice = ("The following are synchronization APIs that block the"
        " host until all issued CUDA calls are complete.\n\n"
        "Suggestions:\n"
        "   1. Avoid excessive use of synchronization.\n"
        "   2. Use asynchronous CUDA event calls, such as cudaStreamWaitEvent()"
        " and cudaEventSynchronize(), to prevent host synchronization.")

    message_noresult = ("There were no problems detected related to"
        " synchronization APIs.")

    query_sync_memset = """
    WITH
        {MEM_KIND_STRS_CTE}
        sid AS (
            SELECT
                *
            FROM
                StringIds
            WHERE
                    value LIKE 'cudaMemset%'
                AND value NOT LIKE '%async%'
        ),
        memset AS (
            SELECT
                *
            FROM
                CUPTI_ACTIVITY_KIND_MEMSET
            WHERE
                   memKind == 1
                OR memKind == 4
        )
    SELECT
        memset.end - memset.start AS "Duration:dur_ns",
        memset.start AS "Start:ts_ns",
        mk.name AS "Memory Kind",
        memset.bytes AS "Bytes:mem_B",
        (memset.globalPid >> 24) & 0x00FFFFFF AS "PID",
        memset.deviceId AS "Device ID",
        memset.contextId AS "Context ID",
        memset.streamId AS "Stream ID",
        sid.value AS "API Name",
        memset.globalPid AS "_Global ID",
        'cuda' AS "_API"
    FROM
        memset
    JOIN
        sid
        ON sid.id == runtime.nameId
    JOIN
        main.CUPTI_ACTIVITY_KIND_RUNTIME AS runtime
        ON runtime.correlationId == memset.correlationId
    LEFT JOIN
        MemKindStrs AS mk
        ON memKind == mk.id
    ORDER BY
        1 DESC
    LIMIT {ROW_LIMIT}
"""

    table_checks = {
        'CUPTI_ACTIVITY_KIND_RUNTIME':
            "{DBFILE} could not be analyzed because it does not contain the required CUDA data."
            " Does the application use CUDA runtime APIs?",
        'CUPTI_ACTIVITY_KIND_MEMSET':
            "{DBFILE} could not be analyzed because it does not contain the required CUDA data."
            " Does the application use CUDA memset APIs?"
    }

    def setup(self):
        err = super().setup()
        if err != None:
            return err

        self.query = self.query_sync_memset.format(
            MEM_KIND_STRS_CTE = self.MEM_KIND_STRS_CTE,
            ROW_LIMIT = self._row_limit)

if __name__ == "__main__":
    SyncMemset.Main()
