#!/usr/bin/env python

import nsysstats

class CUDAGPUKernelSummary(nsysstats.StatsReport):

    display_name = 'CUDA GPU Kernel Summary'
    usage = f"""{{SCRIPT}}[:base|:mangled] -- CUDA GPU Kernel Summary

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
        Time : Percentage of "Total Time"
        Total Time : Total time used by all executions of this kernel
        Instances: Number of calls to this kernel
        Avg : Average execution time of this kernel
        Med : Median exectuion time of this kernel
        Min : Smallest execution time of this kernel
        Max : Largest execution time of this kernel
        StdDev : Standard deviation of the time of this kernel
        GridXYZ : Grid dimensions for kernel launch call
        BlockXYZ : Block dimensions for kernel launch call
        Name : Name of the kernel

    This report provides a summary of CUDA kernels and their execution times.
    Kernels are sorted by grid dimensions, block dimensions, and kernel name.
    Note that the "Time" column is calculated using a summation of the "Total
    Time" column, and represents that kernel's percent of the execution time
    of the kernels listed, and not a percentage of the application wall or
    CPU execution time.
"""

    query_stub = """
WITH
    summary AS (
        SELECT
            coalesce({NAME_COL_NAME}, demangledName) AS nameId,
            sum(end - start) AS total,
            count(*) AS num,
            avg(end - start) AS avg,
            median(end - start) AS med,
            min(end - start) AS min,
            max(end - start) AS max,
            stdev(end - start) AS stddev,
            printf('%4d %4d %4d', gridX, gridY, gridZ) AS grid,
            printf('%4d %4d %4d', blockX, blockY, blockZ) AS block
        FROM
            CUPTI_ACTIVITY_KIND_KERNEL
        GROUP BY 1, grid, block
    ),
    totals AS (
        SELECT sum(total) AS total
        FROM summary
    )
SELECT
    round(summary.total * 100.0 / (SELECT total FROM totals), 1) AS "Time:ratio_%",
    summary.total AS "Total Time:dur_ns",
    summary.num AS "Instances",
    round(summary.avg, 1) AS "Avg:dur_ns",
    round(summary.med, 1) AS "Med:dur_ns",
    summary.min AS "Min:dur_ns",
    summary.max AS "Max:dur_ns",
    round(summary.stddev, 1) AS "StdDev:dur_ns",
    grid AS "GridXYZ",
    block AS "BlockXYZ",
    ids.value AS "Name"
FROM
    summary
LEFT JOIN
    StringIds AS ids
    ON ids.id == summary.nameId
ORDER BY 2 DESC
;
"""

    table_checks = {
        'CUPTI_ACTIVITY_KIND_KERNEL':
            '{DBFILE} does not contain CUDA kernel data.'
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
    CUDAGPUKernelSummary.Main()
