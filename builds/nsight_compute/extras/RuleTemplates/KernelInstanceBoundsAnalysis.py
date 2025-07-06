import NvRules
import nvanalysis

def get_name():
    return "Kernel Bounds Analysis"

def get_description():
    return "Kernel Bounds Analysis Rule"

def get_identifier():
    return "kernel_instance_bounds_analysis"

def get_section_identifier():
    return "kernel_instance_bounds_analysis"

#def evaluate(handle):
     # List rules proposed by this rule here once they are ready
#    return NvRules.require_rules(handle, [ "kernel_instance_latency_analysis" ])

def apply(handle):

    ctx = NvRules.get_context(handle)
    fe = ctx.frontend()
    action = ctx.range_by_idx(0).action_by_idx(0)

    inst_issued_slots = action.metric_by_name("smsp__inst_issued_slots").as_double()
    inst_executed_lsu_pipe = action.metric_by_name("smsp__inst_executed_lsu_pipe").as_double()
    inst_executed_tex_pipe = action.metric_by_name("smsp__inst_executed_tex_pipe").as_double()
    inst_executed_bru_pipe = action.metric_by_name("smsp__inst_executed_bru_pipe").as_double()
    utilization_issue = action.metric_by_name("smsp__utilization_issue").as_double()

    m_issue_slots = inst_issued_slots
    m_ldst_issued = inst_executed_lsu_pipe + inst_executed_tex_pipe
    m_cf_issued = inst_executed_bru_pipe
    m_issue_slot_utilization = utilization_issue

    m_cc_major = action.metric_by_name("device__attribute_compute_capability_major").as_uint64()
    m_cc_minor = action.metric_by_name("device__attribute_compute_capability_minor").as_uint64()
    m_device_name = action.metric_by_name("device__attribute_display_name").as_string()

    avail_mem_unit_enums = nvanalysis.units.get_memory_unit_enums(m_cc_major, m_cc_minor)
    avail_func_unit_enums = nvanalysis.units.get_function_unit_enums(m_cc_major, m_cc_minor)

    avail_mem_units = []
    avail_func_units = []
    for memUnitEnum in avail_mem_unit_enums:
        avail_mem_units.append(nvanalysis.units.get_memory_unit(memUnitEnum))
    for funcUnitEnum in avail_func_unit_enums:
        avail_func_units.append(nvanalysis.units.get_function_unit(funcUnitEnum))

    slot_utlization = m_issue_slot_utilization / 100.0
    max_utilized_function_unit = nvanalysis.units.get_max_utilized_function_unit(avail_func_units, action)
    fu_utilization = nvanalysis.metrics.get_utilization_percent(max_utilized_function_unit.value(action))

    # TODO
    # if (memMap.containsKey(MemoryUnit.SYSMEM))  ...

    (mem_util, mem_bound) = nvanalysis.units.get_memory_utilization(avail_func_units, avail_mem_units, action)

    if fu_utilization > slot_utlization:
        bound = nvanalysis.bounds.get_kernel_bound(fu_utilization, mem_util)
    else:
        cf_util = slot_utlization * (m_cf_issued / m_issue_slots)
        ldst_util = slot_utlization * (m_ldst_issued / m_issue_slots)
        arith_util = slot_utlization - cf_util - ldst_util

        sm_util = slot_utlization - ldst_util
        bound = nvanalysis.bounds.get_kernel_bound(sm_util, mem_util)

    fe.message(nvanalysis.messages.kernel_bounds_msg(bound, mem_bound, m_device_name))

