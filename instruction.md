# 供热末端的支路归属与泵功交接

供热调度员在入冬前整理一栋园区的换热站记录。现场保留了若干支路传感器的回水温度曲线和压力读数，以及各用户端点的流量、负荷温度和压力读数；支路编号与用户端点之间的对应关系在一次巡检表合并时中断。换热站保留供回水温度与总热量的聚合历程。调度组要在投料评审前恢复支路归属、热滞后和散热系数，再选择一组泵档位，让总泵功尽量低。

`heat_network.py` 的输入是管网 JSON，输出参数指向交接目录。

终端在正常完成时保持无文本输出。

管网交接 JSON 的最外层字段为 schema_version,run_id,network_id,terminal_demands,branch_records,aggregate_observations,pump_options,trunk_limit_m3h。

`schema_version` 必须是整数 `1`，布尔值、字符串、浮点数和其它整数均拒收。成功报告中的 `schema_version` 也必须写成整数 `1`。

用户端点记录包含 terminal_id,flow_profile,load_temp_profile,required_flow_m3h,static_pressure_pa 五个槽位。

支路记录包含 record_id,return_temp,pressure_pa 三个槽位。

聚合观测包含 heat_flow,supply_temp,return_temp 三个槽位。

泵档记录包含 record_id,option_id,capacity_m3h,power_kw 四个槽位。

flow_profile、load_temp_profile、return_temp、heat_flow、supply_temp 和聚合 return_temp 各有 8 个数值。

record_id 与 terminal_id 不得重复。

布尔值不能通过计量校验。

接管名单最多包含 8 条支路和 8 个端点。

每个支路记录都必须有至少一个泵选项。

热滞后取 0 到 4 个时间步，步长为五分钟。

散热系数位于 0 到 0.45。

支路在 t 时刻的温度按负荷温度先平移 lag_steps 个时间步，再向 18 摄氏度环境温度衰减；t 小于滞后步数时沿用序列首点。

聚合热量按各支路平移后的流量、供回水温差和散热系数求和；聚合回水温度按同一组平移流量和散热系数加权。

交接目录生成报告、追加账本和检查点三类交付件。

文档排成单行对象文本，UTF-8，行尾一次换行。

供热输出对象包含七个成员：schema_version,run_id,network_id,assignments,topology,selection,summary。

支路映射行保留 record_id,terminal_id,lag_steps,loss_fraction 四项。

拓扑行只携带 terminal_id 与 parent_terminal_id。

泵选择行保留 terminal_id,record_id,option_id,power_kw 四项。

报告汇总区记录 branch_count,total_power_kw,trunk_flow_m3h。

归属步骤要用支路回水轨迹拟合每个候选端点的滞后和散热系数，再用聚合热量和聚合回水温度闭合对应关系，不能从输入直接抄出完整映射。

topology 按 static_pressure_pa 从小到大连接最近的更低压力端点，根节点的 parent_terminal_id 为 null。

loss_fraction 保留六位小数，且不能为负。

lag_steps 是整数，按五分钟一步计数。

selection 按 terminal_id 升序排列。

同一管网快照的报告字节保持一致。

账本条目数在快照重放后保持稳定。

新的快照摘要会单独占一条账目。

input_digest,status,run_id,branch_count,terminal_count 是管网账本单行的字段。

检查点先记录 terminal_count，再依次保存 run_id,input_digest,report_digest。

管网结构若缺键、含多余键或重复键，计算立即停止。

input refused: 后面的原因只写进标准错误。

交接失败时原有报告、追加账本和检查点必须维持原状。

泵选项总容量需要落在 trunk_limit_m3h 以内。

交接结果要能让下一班从聚合历程复核用户归属、热滞后、散热系数和泵功选择。
