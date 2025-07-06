import NvRules

def get_identifier():
    return "TemplateRule2"

def get_name():
    return "Advanced Template Rule"

def get_description():
    return "Another rule template, demonstrating more advanced NvRules functionality"

def get_section_identifier():
    # map to the same template section as TemplateRule1
    return "RuleTemplateSection"

def apply(handle):
    ctx = NvRules.get_context(handle)
    fe = ctx.frontend()

    action = ctx.range_by_idx(0).action_by_idx(0)

    # add two new metrics to this actions
    # any existing metric with the same name would be overridden
    action.add_integer_metric("new_metric_numeric", NvRules.IMetric.ValueKind_UINT64, 42)
    action.add_string_metric("new_metric_string", NvRules.IMetric.ValueKind_STRING, "Hello world")

    # prove that we can retrieve the newly-added metric again
    mStr = action.metric_by_name("new_metric_string")
    fe.message("Added metric " + mStr.name() + " with value '" + mStr.as_string() + "'")

    # load a table, it might now use our new metrics as well
    fe.load_chart_from_file("RuleTemplate2_table.chart")
