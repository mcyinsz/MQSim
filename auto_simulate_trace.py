import os
import shutil
import subprocess
import multiprocessing
import xml.etree.ElementTree as ET
import glob
import time
import csv
from typing import Dict, List, Any

# ================= 配置区域 =================
MQSIM_EXEC_PATH = "./MQSim"  # 请确保路径正确
HW_CONFIG_PATH = "./HBF_workspace/hbfconfig.xml" 
TEMP_WORKSPACE_ROOT = "./temp_sim_workspace/temp_periodic_sim_workspace"
CSV_RESULT_PATH = "periodic_test_results.csv"
# ===========================================

def generate_trace_file(trace_path: str, params: Dict[str, Any]):
    """
    生成周期性批量请求的 Trace 文件。
    
    关键参数 params:
    - Total_Data_Size_MB: 总数据量 (MB)
    - Request_Size_KB: 单个请求大小 (KB)
    - Batch_Size_KB: 每次发送的一批数据总大小 (KB)。如果等于 Request_Size_KB，就是逐个发送。
    - Interval_NS: 批次之间的等待时间 (纳秒)。
    - Is_Read: 读写类型
    """
    # 1. 基础参数解析
    total_size_mb = params.get("Total_Data_Size_MB", 100)
    req_size_kb = params.get("Request_Size_KB", 16) # 默认 16KB
    batch_size_kb = params.get("Batch_Size_KB", 128) # 默认一批发 128KB
    interval_ns = params.get("Interval_NS", 1000) # 默认间隔 1us
    is_read = params.get("Is_Read", True)
    
    req_type = 1 if is_read else 0
    
    # 2. 单位换算
    # Sector Size = 512 Bytes
    req_size_bytes = req_size_kb * 1024
    req_size_sectors = req_size_bytes // 512
    
    total_bytes = total_size_mb * 1024 * 1024
    total_requests = total_bytes // req_size_bytes
    
    # 计算一个 Batch 包含多少个 Request
    # 比如 Batch=128KB, Req=16KB -> requests_per_batch = 8
    requests_per_batch = (batch_size_kb * 1024) // req_size_bytes
    if requests_per_batch < 1:
        requests_per_batch = 1 # 保护机制

    current_lba = 0
    current_time_ns = 0
    
    with open(trace_path, "w") as f:
        for i in range(int(total_requests)):
            # 逻辑：
            # 第 0 ~ N-1 个请求 (第一批) -> Time = 0
            # 第 N ~ 2N-1 个请求 (第二批) -> Time = 0 + Interval
            # ...
            
            # 当 i 达到 batch 的整数倍时，增加时间戳
            # 注意：i=0时不加，i=requests_per_batch时加一次
            if i > 0 and i % requests_per_batch == 0:
                current_time_ns += interval_ns
            
            # 格式: Arrival_Time Device_Num Start_LBA Size_Sectors Type
            line = f"{current_time_ns} 0 {current_lba} {req_size_sectors} {req_type}\n"
            f.write(line)
            
            current_lba += req_size_sectors

def generate_workload_xml(xml_filepath: str, trace_filepath: str, params: Dict[str, Any]):
    """
    生成 Trace Based Workload XML (保持不变，除了路径处理)
    """
    
    p = {
        "Channel_IDs": params.get("Channel_IDs", "0"),
        "Chip_IDs": params.get("Chip_IDs", "0"),
        "Die_IDs": params.get("Die_IDs", "0"),
        "Plane_IDs": params.get("Plane_IDs", "0"),
        "Initial_Occupancy_Percentage": params.get("Initial_Occupancy_Percentage", 5),
    }

    abs_trace_path = os.path.abspath(trace_filepath)

    xml_content = f"""<?xml version="1.0" encoding="us-ascii"?>
<MQSim_IO_Scenarios>
	<IO_Scenario>
		<IO_Flow_Parameter_Set_Trace_Based>
			<Priority_Class>URGENT</Priority_Class>
			<Device_Level_Data_Caching_Mode>WRITE_CACHE</Device_Level_Data_Caching_Mode>
			<Channel_IDs>{p['Channel_IDs']}</Channel_IDs>
			<Chip_IDs>{p['Chip_IDs']}</Chip_IDs>
			<Die_IDs>{p['Die_IDs']}</Die_IDs>
			<Plane_IDs>{p['Plane_IDs']}</Plane_IDs>
			<Initial_Occupancy_Percentage>{p['Initial_Occupancy_Percentage']}</Initial_Occupancy_Percentage>
			<File_Path>{abs_trace_path}</File_Path>
			<Percentage_To_Be_Executed>100</Percentage_To_Be_Executed>
			<Relay_Count>1</Relay_Count>
			<Time_Unit>NANOSECOND</Time_Unit>
		</IO_Flow_Parameter_Set_Trace_Based>
	</IO_Scenario>
</MQSim_IO_Scenarios>
"""
    with open(xml_filepath, "w") as f:
        f.write(xml_content)

def parse_result_xml(result_path: str) -> Dict[str, float]:
    try:
        tree = ET.parse(result_path)
        root = tree.getroot()
        io_flow = root.find(".//Host/Host.IO_Flow")
        if io_flow is None: return {"Error": "Cannot find Host.IO_Flow tag"}

        return {
            "IOPS": float(io_flow.find("IOPS").text),
            "Bandwidth_GB": float(io_flow.find("Bandwidth").text) / 1024 / 1024 / 1024, # 转为 GB/s
            "Avg_Lat_us": float(io_flow.find("Device_Response_Time").text), # 转为 us
            "Max_Lat_us": float(io_flow.find("Max_Device_Response_Time").text),
        }
    except Exception as e:
        return {"Error": str(e)}

def worker_task(args):
    case_name, params = args
    run_dir = os.path.join(TEMP_WORKSPACE_ROOT, case_name)
    if os.path.exists(run_dir): shutil.rmtree(run_dir)
    os.makedirs(run_dir)

    try:
        abs_exec = os.path.abspath(MQSIM_EXEC_PATH)
        abs_hw = os.path.abspath(HW_CONFIG_PATH)
        trace_path = os.path.join(run_dir, "workload.trace")
        xml_path = os.path.join(run_dir, "scenario.xml")

        # 1. 生成带时间间隔的 Trace
        generate_trace_file(trace_path, params)
        # 2. 生成 Config
        generate_workload_xml(xml_path, trace_path, params)

        # 3. 运行
        cmd = [abs_exec, "-i", abs_hw, "-w", os.path.abspath(xml_path)]
        with open(os.path.join(run_dir, "sim.log"), "w") as log:
            subprocess.run(cmd, cwd=run_dir, stdout=log, stderr=subprocess.STDOUT, check=True, input=b'\t')

        # 4. 解析
        xml_files = glob.glob(os.path.join(run_dir, "*.xml"))
        res_file = None
        for f in xml_files:
            if "workload.xml" not in f and "hbfconfig" not in f:
                res_file = f
                break
        
        if not res_file: return case_name, params, {"Error": "No result file"}
        
        metrics = parse_result_xml(res_file)

        shutil.rmtree(run_dir) 
        return case_name, params, metrics

    except Exception as e:
        return case_name, params, {"Error": str(e)}

class MQSimExperiment:
    def __init__(self, max_workers=None):
        self.max_workers = max_workers
        if not os.path.exists(TEMP_WORKSPACE_ROOT): os.makedirs(TEMP_WORKSPACE_ROOT)

    def run(self, scenarios: Dict[str, Dict[str, Any]]):
        tasks = [(name, p) for name, p in scenarios.items()]
        print(f"[*] Starting {len(tasks)} periodic workload simulations...")
        
        results_data = []

        with multiprocessing.Pool(self.max_workers) as pool:
            for i, (name, params, metrics) in enumerate(pool.map(worker_task, tasks)):
                if "Error" in metrics:
                    print(f"[{i+1}/{len(tasks)}] {name} -> ERROR: {metrics['Error']}")
                else:
                    print(f"[{i+1}/{len(tasks)}] {name} | Batch: {params['Batch_Size_KB']}KB | "
                          f"Intv: {params['Interval_NS']}ns -> BW: {(metrics['Bandwidth_GB']):.1f} GB/s, "
                          f"Lat: {metrics['Avg_Lat_us']:.2f} us")
                
                # 合并数据用于 CSV
                row = {"Case_Name": name}
                row.update(params) # 记录输入参数
                row.update(metrics) # 记录输出指标
                results_data.append(row)

        self.export_csv(results_data)

    def export_csv(self, data: List[Dict]):
        if not data: return
        keys = ["Case_Name", "Total_Data_Size_MB", "Request_Size_KB", "Batch_Size_KB", "Interval_NS", 
                "IOPS", "Bandwidth_MB", "Avg_Lat_us", "Max_Lat_us", "Error"]
        # 确保所有键都在，防止因为Error导致key缺失
        final_keys = [k for k in keys if k in keys] 
        
        with open(CSV_RESULT_PATH, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=final_keys, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(data)
        print(f"\n[*] Results exported to {os.path.abspath(CSV_RESULT_PATH)}")

if __name__ == "__main__":
    if not os.path.exists(MQSIM_EXEC_PATH):
        print("MQSim executable not found.")
        exit(1)

    # ================= 实验设计区域 =================
    
    # 固定参数
    FIXED_TOTAL_SIZE_MB = 100  # 总共读 100MB
    FIXED_REQ_SIZE_KB = 256     # 每个 IO 16KB
    
    scenarios = {}

    # 变量 1: Batch Size (一批发多少数据)
    # 从 16KB (每次发1个) 到 4MB (每次发256个)
    batch_sizes_kb = [256] 
    
    # 变量 2: Interval (批次之间的间隔时间, 纳秒)
    # 从 0 (极致Burst) 到 1ms (极慢)
    # 500ns, 1us, 5us, 10us, 50us, 100us, 500us
    intervals_ns = [100]

    # 生成组合
    for b_kb in batch_sizes_kb:
        for intv in intervals_ns:
            # 命名: Batch_128KB_Intv_1000ns
            name = f"Batch_{b_kb}KB_Intv_{intv}ns"
            
            scenarios[name] = {
                "Total_Data_Size_MB": FIXED_TOTAL_SIZE_MB,
                "Request_Size_KB": FIXED_REQ_SIZE_KB,
                "Batch_Size_KB": b_kb,
                "Interval_NS": intv,
                "Is_Read": True,
                
                # 硬件全开，保证瓶颈不在通道数限制上（除非那是你想测的）
                "Channel_IDs": "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31", 
                "Chip_IDs": "0",
                "Die_IDs": "0",
                "Plane_IDs": "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,71,72,73,74,75"
            }

    # 运行实验
    # 建议根据你的 CPU 核心数设置 worker 数量，MQSim 是单线程程序，多进程跑非常快
    exp = MQSimExperiment(max_workers=12) 
    exp.run(scenarios)
