#!/usr/bin/env python

import nsysstats

class UMCPUPageFaultsSummary(nsysstats.StatsReport):

    display_name = 'Unified Memory CPU Page Faults Summary'
    usage = f"""{{SCRIPT}} -- Unified Memory CPU Page Faults Summary

    Output:
        CPU Page Faults : Number of CPU page faults that occurred
        CPU Instruction Address : Address of the CPU instruction that caused the CPU page faults

    This report provides a summary of CPU page faults for unified memory.
"""

    query = """
WITH
    summary AS (
        SELECT
            CpuInstruction AS sourceId,
            count(*) AS num
        FROM
            CUDA_UM_CPU_PAGE_FAULT_EVENTS
        GROUP BY 1
    )
SELECT
    summary.num AS "CPU Page Faults",
    ids.value AS "CPU Instruction Address"
FROM
    summary
LEFT JOIN
    StringIds AS ids
    ON ids.id == summary.sourceId
ORDER BY 1 DESC -- CPU Page Faults
;
"""

    table_checks = {
        'CUDA_UM_CPU_PAGE_FAULT_EVENTS':
            "{DBFILE} does not contain CUDA Unified Memory CPU page faults data."
    }

if __name__ == "__main__":
    UMCPUPageFaultsSummary.Main()
