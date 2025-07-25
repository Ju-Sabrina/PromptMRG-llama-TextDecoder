#!/usr/bin/env python

import gpustats

class GPUStarvation(gpustats.GPUOperation):

    DEFAULT_GAP = 500

    display_name = "GPU Starvation"
    usage = f"""{{SCRIPT}}[:options...] -- GPU Starvation

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

        gap=<threshold> - Minimum duration of GPU gaps in milliseconds.
            Default is {DEFAULT_GAP}ms.

    Output: All time values default to nanoseconds
        Row# : Row number of the GPU gap
        Duration : Duration of the GPU gap
        Start : Start time of the GPU gap
        PID : Process identifier
        Device ID : GPU device identifier

    This rule identifies time regions where a GPU is idle for longer than a set
    threshold. For each process, each GPU device is examined, and gaps are
    found within the time range that starts with the beginning of the first GPU
    operation on that device and ends with the end of the last GPU operation on
    that device. GPU gaps that cannot be addressed by the user are excluded.
"""

    message_advice = ("The following are ranges where a GUP is idle for more"
        " than {GAP}ms. Addressing these gaps might imporve application"
        " performance\n\n"
        "Suggestions:\n"
        "   1. Use GPU sampling data, OS Runtime blocked state backgraces,"
        " and/or OS Runtime APIs related to thread synchronization to"
        " understand if a sluggish or blocked CPU is causing these gaps.\n"
        "   2. Add NVTX annotations to CPU code to understand the reason behind"
        " these gaps.")

    message_noresult = ("There were no problems detected with GPU"
        " utilization. GPU was not found to be idle for more than {GAP}ms.")

    def MessageAdvice(self, extended=True):
        return self.message_advice.format(GAP=self._gap)

    def MessageNoResult(self):
        return self.message_noresult.format(GAP=self._gap)

    query_format_columns = """
    SELECT
        ROW_NUMBER() OVER(ORDER BY duration DESC, gapStart) AS "Row#",
        duration AS "Duration:dur_ns",
        gapStart AS "Start:ts_ns",
        pid AS "PID",
        deviceId AS "Device ID",
        contextId AS "Context ID",
        globalId AS "_Global ID",
        api AS "_API"
    FROM
        ({GPU_UNION_TABLE})
    LIMIT {ROW_LIMIT}
"""

# Find gaps.
# "ops" is the table containing GPU operations + profiling overhead.
# 1. CTE "starts": Give a rowNum, SRi, to each start, ordered by start time.
# 2. CTE "ends": Give a rowNum, ERj, to each end, ordered by end time.
# 3. Reconstruct intervals [ERj, SRj+1] by putting together an end ERj with the
#    next start SRj+1 (start_rowNum - 1 = end_rowNum).
# 4. Keep only those intervals [ERj, SRj+1] that are valid (ERj < SRj+1).
#
# Assume that we have the following intervals:
#
# SR1                          ER2
#  |--------------a-------------|
#      SR2                ER1
#       |---------b--------|
#                                         SR3              ER3
#                                          |--------c-------|
# With step 3, we get:
# 1. ER1 joined with SR2.
# 2. ER2 joined with SR3.
#
#      SR2                 ER1
#       |---------a'--------|
#                               ER2        SR3
#                                |----b'----|
#
# Only the second interval (b') meets the condition end < start of step 4 and
# will be considered as a gap. (a') will be discarded and the query will
# return:
#
#                               ER2        SR3
#                                |----b'----|
#
# ER2 will be the start and SR3 will be the end of the gap.
    query_gap = """
    WITH
        ops AS (
            {{GPU_TABLE}}
        ),
        starts AS (
            SELECT
                ROW_NUMBER() OVER(ORDER BY pid, deviceId, start) AS rowNum,
                start,
                pid,
                deviceId
            FROM
                ops
        ),
        ends AS (
            SELECT
                ROW_NUMBER() OVER(ORDER BY pid, deviceId, end) AS rowNum,
                end,
                pid,
                globalId,
                deviceId,
                contextId,
                api
            FROM
                ops
        )
    SELECT
        start - end AS duration,
        end AS gapStart,
        start AS gapEnd,
        ends.pid,
        ends.globalId,
        ends.deviceId,
        ends.contextId,
        ends.api
    FROM
        starts
    JOIN
        ends
        ON      starts.rowNum - 1 == ends.rowNum
            AND starts.deviceId == ends.deviceId
            AND starts.pid == ends.pid
    WHERE
            duration > {THRESHOLD}
        AND gapStart < gapEnd
    LIMIT {ROW_LIMIT}
"""

    _arg_opts = [
        [['gap'],{'default': DEFAULT_GAP, 'type': int,
            'help': 'minimum gap size, in milliseconds'}],
    ]

    def setup(self):
        err = super().setup()
        if err != None:
            return err

        self._gap = self.parsed_args.gap

        err = self.create_gpu_ops_view(self.query_gap.format(
            THRESHOLD = self._gap * 1000000,
            ROW_LIMIT = self._row_limit))
        if err != None:
            return err

        self.query = self.query_format_columns.format(
            GPU_UNION_TABLE = self.query_gpu_ops_union(),
            ROW_LIMIT = self._row_limit)

if __name__ == "__main__":
    GPUStarvation.Main()
