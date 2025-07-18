#!/usr/bin/env python

import nsysstats

class UMTotalsSummary(nsysstats.StatsReport):

    usage = f"""{{SCRIPT}} -- Unified Memory Totals Summary

    Output:
        Total HtoD Migration Size : Total bytes transferred from host to device
        Total DtoH Migration Size : Total bytes transferred from device to host
        Total CPU Page Faults : Total number of CPU page faults that occured
        Total GPU Page Faults : Total number of GPU page faults that occured
        Minimum Virtual Address : Minimum value of the virtual address range for the pages transferred
        Maximum Virtual Address : Maximum value of the virtual address range for the pages transferred

    This report provides a summary of all the page faults for unified memory.
"""

    display_name = 'Unified Memory Totals Summary'
    query = """
WITH
    cpuSummary AS (
        SELECT
            min(address) AS minVirtualAddress,
            max(address) AS maxVirtualAddress,
            count(*) AS num
        FROM
            CUDA_UM_CPU_PAGE_FAULT_EVENTS
    ),
    gpuSummary AS (
        SELECT
            min(address) AS minVirtualAddress,
            max(address) AS maxVirtualAddress,
            sum(numberOfPageFaults) AS num
        FROM
            CUDA_UM_GPU_PAGE_FAULT_EVENTS
    ),
    d2hTransferSummary AS (
        SELECT
            min(virtualAddress) AS minVirtualAddress,
            max(virtualAddress) AS maxVirtualAddress,
            sum(end-start) AS transferDuration,
            sum(bytes) AS D2H
        FROM
            CUPTI_ACTIVITY_KIND_MEMCPY
        WHERE copyKind = 12
    ),
    h2dTransferSummary AS (
        SELECT
            min(virtualAddress) AS minVirtualAddress,
            max(virtualAddress) AS maxVirtualAddress,
            sum(end-start) AS transferDuration,
            sum(bytes) AS H2D
        FROM
            CUPTI_ACTIVITY_KIND_MEMCPY
        WHERE copyKind = 11
    ),
    ranges AS (
        SELECT
            min(cpu.minVirtualAddress, gpu.minVirtualAddress,
                h2d.minVirtualAddress, d2h.minVirtualAddress) AS minAddr,
            max(cpu.maxVirtualAddress, gpu.maxVirtualAddress,
                h2d.maxVirtualAddress, d2h.maxVirtualAddress) AS maxAddr
        FROM
            cpuSummary AS cpu,
            gpuSummary AS gpu,
            h2dTransferSummary AS h2d,
            d2hTransferSummary AS d2h
    )
SELECT
    h2dTransferSummary.h2d AS "Total HtoD Migration Size:mem_B",
    d2hTransferSummary.d2h AS "Total DtoH Migration Size:mem_B",
    cpuSummary.num AS "Total CPU Page Faults",
    gpuSummary.num AS "Total GPU PageFaults",
    ranges.minAddr AS "Minimum Virtual Address:addr_uint",
    ranges.maxAddr AS "Maximum Virtual Address:addr_uint"
FROM
    ranges, cpuSummary, gpuSummary, d2hTransferSummary, h2dTransferSummary
;
"""

    table_checks = {
        'CUPTI_ACTIVITY_KIND_MEMCPY':
            "{DBFILE} does not contain CUDA memory transfers data.",
        'CUDA_UM_CPU_PAGE_FAULT_EVENTS':
            "{DBFILE} does not contain CUDA Unified Memory CPU page faults data.",
        'CUDA_UM_GPU_PAGE_FAULT_EVENTS':
            "{DBFILE} does not contain CUDA Unified Memory GPU page faults data.",
    }

if __name__ == "__main__":
    UMTotalsSummary.Main()
