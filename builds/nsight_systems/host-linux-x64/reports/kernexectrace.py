#!/usr/bin/env python

import nsysstats

class CUDAKernelExecTrace(nsysstats.StatsReport):

    display_name = 'Kernel Launch & Exec Time Trace'
    usage = f"""{{SCRIPT}}[:base|:mangled] -- Kernel launch and exec time trace

    base - Optional argument, if given, will cause summary to be over the
        base name of the kernel, rather than the templated name.

    mangled - Optional argument, if given, will cause summary to be over the
        raw mangled name of the kernel, rather than the templated name.

        Note: the ability to display mangled names is a recent addition to the
        report file format, and requires that the profile data be captured with
        a recent version of Nsys. Re-exporting an existing report file is not
        sufficient. If the raw, mangled kernel name data is not available, the
        default demangled names will be used.

    Output: All time values default to nanoseconds
        API Start : Start timestamp of CUDA API launch call
        API Dur : Duration of CUDA API launch call
        Queue Start : Start timestamp of queue wait time, if it exists
        Queue Dur : Duration of queue wait time, if it exists
        Kernel Start : Start timestamp of CUDA kernel
        Kernel Dur : Duration of CUDA kernel
        Total Dur : Duration from API start to kernel end
        PID : Process ID that made kernel launch call
        TID : Thread ID that made kernel launch call
        DevId : CUDA Device ID that executed kernel (which GPU)
        API Function : Name of CUDA API call used to launch kernel
        GridXYZ : Grid dimensions for kernel launch call
        BlockXYZ : Block dimensions for kernel launch call
        Kernel Name : Name of CUDA Kernel

    This report provides a trace of the launch and execution time of each CUDA
    kernel. The launch and execution is broken down into three phases: "API
    time," the execution time of the CUDA API call on the CPU used to launch the
    kernel; "Queue time," the time between the launch call and the kernel
    execution; and "Kernel time," the kernel execution time on the GPU. The
    "total time" is not a just sum of the other times, as the phases sometimes
    overlap. Rather, the total time runs from the start of the API call to end
    of the API call or the end of the kernel execution, whichever is later.

    The reported queue time is measured from the end of the API call to the
    start of the kernel execution. The actual queue time is slightly longer, as
    the kernel is enqueue somewhere in the middle of the API call, and not in
    the final nanosecond of function execution. Due to this delay, it is
    possible for kernel execution to start before the CUDA launch call returns.
    In these cases, no queue times will be reported.

    Be aware that having a queue time is not inherently bad. Queue times
    indicate that the GPU was busy running other tasks when the new kernel was
    scheduled for launch. If every kernel launch is immediate, without any queue
    time, that _may_ indicate an idle GPU with poor utilization. In terms of
    performance optimization, it should not necessarily be a goal to eliminate
    queue time.
"""

    query_stub = """

SELECT
    r.start AS "API Start:ts_ns",
    r.end - r.start AS "API Dur:dur_ns",
    iif(k.start - r.end >= 0, r.end, NULL) AS "Queue Start:ts_ns",
    iif(k.start - r.end >= 0, k.start - r.end, NULL) AS "Queue Dur:dur_ns",
    k.start AS "Kernel Start:ts_ns",
    k.end - k.start AS "Kernel Dur:dur_ns",
    max(r.end, k.end) - r.start AS "Total Dur:dur_ns",
    (r.globalTid >> 24) & 0x00FFFFFF AS PID,
    r.globalTid & 0x00FFFFFF AS TID,
    k.deviceId AS DevId,
    CASE substr(rname.value, -6, 2)
        WHEN '_v'
            THEN substr(rname.value, 1, length(rname.value)-6)
        ELSE rname.value
    END AS "API Function",
    printf('%4d %4d %4d', k.gridX, k.gridY, k.gridZ) AS "GridXYZ",
    printf('%4d %4d %4d', k.blockX, k.blockY, k.blockZ) AS "BlockXYZ",
    kname.value AS "Kernel Name"
FROM
    CUPTI_ACTIVITY_KIND_KERNEL AS k
JOIN
    CUPTI_ACTIVITY_KIND_RUNTIME AS r
    ON      k.correlationId == r.correlationId
        AND k.globalPid == (r.globalTid & 0xFFFFFFFFFF000000)
LEFT JOIN
    StringIds AS rname
    ON r.nameId == rname.id
LEFT JOIN
    StringIds AS kname
    ON kname.id == coalesce(k.{NAME_COL_NAME}, k.demangledName)
ORDER BY 1
"""

    table_checks = {
        'CUPTI_ACTIVITY_KIND_KERNEL':
            "{DBFILE} does not contain CUDA kernel data.",
        'CUPTI_ACTIVITY_KIND_RUNTIME':
            "{DBFILE} does not contain CUDA API data.",
    }

    _arg_opts = [
        [['base'],    {'action': 'store_true'}],
        [['mangled'], {'action': 'store_true'}],
    ]

    def setup(self):
        err = super().setup()
        if err != None:
            return err

        name_col_name = 'demangledName'
        if self.parsed_args.base:
            name_col_name = 'shortName'
        elif (self.parsed_args.mangled and
            self.table_col_exists('CUPTI_ACTIVITY_KIND_KERNEL', 'mangledName')):
            name_col_name = 'mangledName'

        self.query = self.query_stub.format(NAME_COL_NAME = name_col_name)

if __name__ == "__main__":
    CUDAKernelExecTrace.Main()
