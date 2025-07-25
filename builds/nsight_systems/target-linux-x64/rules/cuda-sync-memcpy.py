#!/usr/bin/env python

import nsysstats

class SyncMemcpy(nsysstats.ExpertSystemsReport):

    display_name = "CUDA Synchronous Memcpy"
    usage = f"""{{SCRIPT}}[:options...] -- Synchronous Memcpy

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
        Duration : Duration of memcpy on GPU
        Start : Start time of memcpy on GPU
        Src Kind : Memcpy source memory kind
        Dst Kind : Memcpy destination memory kind
        Bytes : Number of bytes transferred
        PID : Process identifier
        Device ID : GPU device identifier
        Context ID : Context identifier
        Stream ID : Stream identifier
        API Name : Runtime API function name

    This rule identifies memory transfers that are synchronous. It does not
    include cudaMemcpy*() (no Async suffix) occurred within the same device as
    well as H2D copy kind with a memory block of 64 KB or less.
"""

    message_advice = ("The following are synchronous memory transfers that"
        " block the host. This does not include host to device transfers of a"
        " memory block of 64 KB or less.\n\n"
        "Suggestion: Use cudaMemcpy*Async() APIs instead.")

    message_noresult = ("There were no problems detected related to"
        " synchronous memcpy operations.")

    query_sync_memcpy = """
    WITH
        {MEM_KIND_STRS_CTE}
        sid AS (
            SELECT
                *
            FROM
                StringIds
            WHERE
                value LIKE 'cudaMemcpy%'
                AND value NOT LIKE '%Async%'
        ),
        memcpy AS (
            SELECT
                *
            FROM
                CUPTI_ACTIVITY_KIND_MEMCPY
            WHERE
                    NOT (bytes <= 64000 AND copyKind == 1)
                AND NOT (srcDeviceId IS NOT NULL AND srcDeviceId == dstDeviceId)
        )
    SELECT
        memcpy.end - memcpy.start AS "Duration:dur_ns",
        memcpy.start AS "Start:ts_ns",
        msrck.name AS "Src Kind",
        mdstk.name AS "Dst Kind",
        memcpy.bytes AS "Bytes:mem_B",
        (memcpy.globalPid >> 24) & 0x00FFFFFF AS "PID",
        memcpy.deviceId AS "Device ID",
        memcpy.contextId AS "Context ID",
        memcpy.streamId AS "Stream ID",
        sid.value AS "API Name",
        memcpy.globalPid AS "_Global ID",
        memcpy.copyKind AS "_Copy Kind",
        'cuda' AS "_API"
    FROM
        memcpy
    JOIN
        sid
        ON sid.id == runtime.nameId
    JOIN
        main.CUPTI_ACTIVITY_KIND_RUNTIME AS runtime
        ON runtime.correlationId == memcpy.correlationId
    LEFT JOIN
        MemKindStrs AS msrck
        ON srcKind == msrck.id
    LEFT JOIN
        MemKindStrs AS mdstk
        ON dstKind == mdstk.id
    ORDER BY
        1 DESC
    LIMIT {ROW_LIMIT}
"""

    table_checks = {
        'CUPTI_ACTIVITY_KIND_RUNTIME':
            "{DBFILE} could not be analyzed because it does not contain the required CUDA data."
            " Does the application use CUDA runtime APIs?",
        'CUPTI_ACTIVITY_KIND_MEMCPY':
            "{DBFILE} could not be analyzed because it does not contain the required CUDA data."
            " Does the application use CUDA memcpy APIs?"
    }

    def setup(self):
        err = super().setup()
        if err != None:
            return err

        self.query = self.query_sync_memcpy.format(
            MEM_KIND_STRS_CTE = self.MEM_KIND_STRS_CTE,
            ROW_LIMIT = self._row_limit)

if __name__ == "__main__":
    SyncMemcpy.Main()
