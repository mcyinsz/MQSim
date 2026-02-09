import os
import shutil
import subprocess
import multiprocessing
import xml.etree.ElementTree as ET
import glob
import time
from typing import Dict, List, Any, Tuple
import matplotlib.pyplot as plt
import re
import math

# ================= 配置区域 =================
# MQSim 可执行文件的路径 (请修改为你的实际路径)
MQSIM_EXEC_PATH = "./MQSim" 
# 硬件配置文件的路径 (请修改为你的实际路径)
HW_CONFIG_PATH = "./HBF_workspace/hbfconfig_new_single_channel.xml" 
# 临时工作目录的根路径，用于存放并行跑的中间文件
TEMP_WORKSPACE_ROOT = "./temp_sim_workspace/temp_symthesis_chip_parallel_sim_workspace"
PLOTS_SAVE_DIR = "./workload_plots"
# ===========================================

# 常用常量
SECTOR_SIZE = 512
REQ_SIZE_8KB = 16  # 16 * 512B = 8KB
REQ_SIZE_BYTES_8KB = 8192
CHIPS_COUNT = 24

# 扫描 QD 时固定的总传输大小 (16MB)
FIXED_SIZE_MB_SCAN_QD = 16
FIXED_SIZE_SECTORS_SCAN_QD = (FIXED_SIZE_MB_SCAN_QD * 1024 * 1024) // SECTOR_SIZE

def generate_workload_xml(filepath: str, params: Dict[str, Any]):
    """
    根据参数字典生成 Workload XML 文件。
    """
    default_chip_ids = ",".join([str(i) for i in range(CHIPS_COUNT)]) 
    default_channel_ids = "0"
    default_die_ids = "0"
    default_plane_ids = "0"

    p = {
        "Channel_IDs": params.get("Channel_IDs", default_channel_ids),
        "Chip_IDs": params.get("Chip_IDs", default_chip_ids),
        "Die_IDs": params.get("Die_IDs", default_die_ids),
        "Plane_IDs": params.get("Plane_IDs", default_plane_ids),
        "Initial_Occupancy_Percentage": params.get("Initial_Occupancy_Percentage", 10),
        "Average_No_of_Reqs_in_Queue": params.get("Average_No_of_Reqs_in_Queue", 2),
        "Read_Percentage": params.get("Read_Percentage", 100),
        "Address_Distribution": params.get("Address_Distribution", "STREAMING"),
        "Average_Request_Size": params.get("Average_Request_Size", 16),
        "Total_Requests_To_Generate": params.get("Total_Requests_To_Generate", 1024),
        "Seed": params.get("Seed", 123)
    }

    xml_content = f"""<?xml version="1.0" encoding="us-ascii"?>
<MQSim_IO_Scenarios>
	<IO_Scenario>
		<IO_Flow_Parameter_Set_Synthetic>
			<Priority_Class>URGENT</Priority_Class>
			<Device_Level_Data_Caching_Mode>WRITE_CACHE</Device_Level_Data_Caching_Mode>
			
			<Channel_IDs>{p['Channel_IDs']}</Channel_IDs>
			<Chip_IDs>{p['Chip_IDs']}</Chip_IDs>
			<Die_IDs>{p['Die_IDs']}</Die_IDs>
			<Plane_IDs>{p['Plane_IDs']}</Plane_IDs>
			
			<Initial_Occupancy_Percentage>{p['Initial_Occupancy_Percentage']}</Initial_Occupancy_Percentage>
			<Working_Set_Percentage>100</Working_Set_Percentage>
			
			<Synthetic_Generator_Type>QUEUE_DEPTH</Synthetic_Generator_Type>
			<Average_No_of_Reqs_in_Queue>{p['Average_No_of_Reqs_in_Queue']}</Average_No_of_Reqs_in_Queue>
			
			<Read_Percentage>{p['Read_Percentage']}</Read_Percentage>
			<Address_Distribution>{p['Address_Distribution']}</Address_Distribution>
			<Percentage_of_Hot_Region>0</Percentage_of_Hot_Region>
			
			<Generated_Aligned_Addresses>true</Generated_Aligned_Addresses>
			<Address_Alignment_Unit>16</Address_Alignment_Unit>
			
			<Request_Size_Distribution>FIXED</Request_Size_Distribution>
			<Average_Request_Size>{p['Average_Request_Size']}</Average_Request_Size>
			<Variance_Request_Size>0</Variance_Request_Size>
			
			<Seed>{p['Seed']}</Seed>
			<Stop_Time>0</Stop_Time>
			<Total_Requests_To_Generate>{p['Total_Requests_To_Generate']}</Total_Requests_To_Generate>

		</IO_Flow_Parameter_Set_Synthetic>
	</IO_Scenario>
</MQSim_IO_Scenarios>
"""
    with open(filepath, "w") as f:
        f.write(xml_content)

def parse_result_xml(result_path: str) -> Dict[str, float]:
    try:
        tree = ET.parse(result_path)
        root = tree.getroot()
        io_flow = root.find(".//Host/Host.IO_Flow")
        
        if io_flow is None:
            return {"Error": "Cannot find Host.IO_Flow tag"}

        results = {
            "IOPS": float(io_flow.find("IOPS").text),
            "Bandwidth": float(io_flow.find("Bandwidth").text),
            "Avg_Response_Time": float(io_flow.find("Device_Response_Time").text),
            "Min_Response_Time": float(io_flow.find("Min_Device_Response_Time").text),
            "Max_Response_Time": float(io_flow.find("Max_Device_Response_Time").text),
        }
        return results
    except Exception as e:
        return {"Error": str(e)}

def worker_task(args):
    case_name, params = args
    run_dir = os.path.join(TEMP_WORKSPACE_ROOT, case_name)
    if os.path.exists(run_dir):
        shutil.rmtree(run_dir)
    os.makedirs(run_dir)

    try:
        abs_exec_path = os.path.abspath(MQSIM_EXEC_PATH)
        abs_hw_config = os.path.abspath(HW_CONFIG_PATH)
        workload_file = os.path.join(run_dir, "workload.xml")

        generate_workload_xml(workload_file, params)

        cmd = [abs_exec_path, "-i", abs_hw_config, "-w", os.path.abspath(workload_file)]

        start_time = time.time()
        with open(os.path.join(run_dir, "sim.log"), "w") as log_file:
            subprocess.run(
                cmd, 
                cwd=run_dir, 
                stdout=log_file, 
                stderr=subprocess.STDOUT, 
                check=True,
                input=b'\t' 
            )
        duration = time.time() - start_time

        xml_files = glob.glob(os.path.join(run_dir, "*.xml"))
        result_file = None
        for f in xml_files:
            if "workload.xml" not in f and "hbfconfig" not in f:
                result_file = f
                break
        
        if not result_file:
            return case_name, {"Error": "Result XML file not generated"}

        metrics = parse_result_xml(result_file)
        metrics["Simulation_Time_Sec"] = duration
        return case_name, metrics

    except subprocess.CalledProcessError:
        return case_name, {"Error": "MQSim Execution Failed"}
    except Exception as e:
        return case_name, {"Error": str(e)}
    finally:
        shutil.rmtree(run_dir) 

class MQSimAutomator:
    def __init__(self, max_workers=None):
        self.max_workers = max_workers
        if not os.path.exists(TEMP_WORKSPACE_ROOT):
            os.makedirs(TEMP_WORKSPACE_ROOT)

    def run(self, workload_configs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        tasks = []
        for name, params in workload_configs.items():
            tasks.append((name, params))

        print(f"[*] Starting {len(tasks)} simulations using {self.max_workers or 'default'} workers...")
        
        results = {}
        with multiprocessing.Pool(processes=self.max_workers) as pool:
            for name, res in pool.map(worker_task, tasks):
                results[name] = res
                bw = res.get('Bandwidth', 0) / (1000**3) if isinstance(res.get('Bandwidth'), (int, float)) else 0
                print(f"    -> Finished: {name} | BW: {bw:.3f} GB/s")
        
        print("[*] All simulations completed.")
        return results

# ================= 画图与数据处理工具 =================

def parse_scenario_params(case_name: str) -> Dict[str, Any]:
    """
    从文件名中提取参数（QD 或 Total Size）。
    返回一个包含 'sort_key' (用于排序) 和 'display_label' (用于X轴) 的字典。
    同时返回 'x_type' ('QD' 或 'SIZE') 和 'x_value_bytes' (如果是SIZE)。
    """
    # 1. 尝试匹配 QD
    match_qd = re.search(r'_(\d+)QD', case_name)
    if match_qd:
        qd = int(match_qd.group(1))
        return {
            "x_type": "QD",
            "sort_key": qd,
            "display_label": str(qd),
            "x_value": qd
        }
    
    # 2. 尝试匹配 Size (KB, MB, GB, B)
    # 这里的正则要小心，比如 size_8KB
    match_size = re.search(r'_size_(\d+)(KB|MB|GB|B)', case_name, re.IGNORECASE)
    if match_size:
        val = int(match_size.group(1))
        unit = match_size.group(2).upper()
        multiplier = 1
        if unit == "KB": multiplier = 1024
        elif unit == "MB": multiplier = 1024**2
        elif unit == "GB": multiplier = 1024**3
        
        total_bytes = val * multiplier
        return {
            "x_type": "SIZE",
            "sort_key": total_bytes,
            "display_label": f"{val}{unit}",
            "x_value_bytes": total_bytes
        }

    # 默认回退
    return {
        "x_type": "UNKNOWN",
        "sort_key": case_name,
        "display_label": case_name,
        "x_value": 0
    }

def calculate_theoretical_bw(is_read: bool, data_size_bytes: float) -> float:
    """
    计算理论有效带宽 (GB/s)
    Formula: Effective_BW = Data_Size / (Latency + Data_Size / Peak_BW)
    """
    # 1. 设定参数
    if is_read:
        t_lat = 2e-6  # 2us
        bw_peak = 100 * 1e9  # 100 GB/s
    else:
        t_lat = 0
        # Peak Write BW = 8KB * 24 chips / 50us
        # 8192 * 24 / 0.000050 = 3,932,160,000 B/s (~3.93 GB/s)
        bw_peak = (8192 * 24) / 50e-6

    # 2. 计算
    # Time = T_lat + (Data / BW_peak)
    total_time = t_lat + (data_size_bytes / bw_peak)
    effective_bw_bps = data_size_bytes / total_time
    
    return effective_bw_bps / (1000**3) # Convert to GB/s for plotting (MQSim use 1000^3 usually)

def plot_and_save_results(scenario_name: str, results: Dict[str, Any]):
    """
    绘制结果：Bandwidth (左轴) + Latency (右轴) + Theoretical BW Line
    """
    if not os.path.exists(PLOTS_SAVE_DIR):
        os.makedirs(PLOTS_SAVE_DIR)

    # 1. 数据解析
    valid_data = []
    
    is_read_scenario = "READ" in scenario_name.upper()
    is_write_scenario = "WRITE" in scenario_name.upper()
    
    # 判断是 QD 扫描还是 Size 扫描
    # 只要有一个 case 解析出来是 SIZE，就认为是 SIZE 扫描
    x_axis_type = "QD" 
    
    for case_name, data in results.items():
        if "Error" in data:
            continue
        
        parsed = parse_scenario_params(case_name)
        if parsed["x_type"] == "SIZE":
            x_axis_type = "SIZE"
            
        valid_data.append({
            "name": case_name,
            "parsed": parsed,
            "bw_gb": data.get("Bandwidth", 0) / (1000**3),
            "lat_us": data.get("Avg_Response_Time", 0)
        })

    if not valid_data:
        print(f"[!] No valid data to plot for {scenario_name}.")
        return

    # 排序
    valid_data.sort(key=lambda x: x["parsed"]["sort_key"])

    # 准备绘图数据
    x_labels = [item["parsed"]["display_label"] for item in valid_data]
    y_bw = [item["bw_gb"] for item in valid_data]
    y_lat = [item["lat_us"] for item in valid_data]
    
    # --- 计算理论线数据 ---
    y_theoretical = []
    for item in valid_data:
        if x_axis_type == "SIZE":
            # 随 Size 变化
            size_bytes = item["parsed"]["x_value_bytes"]
            theo = calculate_theoretical_bw(is_read_scenario, size_bytes)
            y_theoretical.append(theo)
        else:
            # QD 扫描，Size 固定为 16MB
            fixed_bytes = FIXED_SIZE_MB_SCAN_QD * 1024 * 1024
            theo = calculate_theoretical_bw(is_read_scenario, fixed_bytes)
            y_theoretical.append(theo)

    # 2. 绘图
    fig, ax1 = plt.subplots(figsize=(12, 7))

    # X轴标签
    x_axis_label = "Total Transfer Size" if x_axis_type == "SIZE" else "Queue Depth (QD)"
    ax1.set_xlabel(x_axis_label, fontsize=12)
    
    # 左轴: Bandwidth
    color_bw = 'tab:blue'
    ax1.set_ylabel('Bandwidth (GB/s)', color=color_bw, fontsize=12, fontweight='bold')
    ln1 = ax1.plot(x_labels, y_bw, color=color_bw, marker='o', linestyle='-', linewidth=2, label='Simulated BW')
    
    # 增加理论线 (也在左轴)
    color_theo = 'green'
    ln_theo = ax1.plot(x_labels, y_theoretical, color=color_theo, marker='', linestyle='--', linewidth=2, label='Theoretical BW')

    ax1.tick_params(axis='y', labelcolor=color_bw)
    ax1.grid(True, linestyle='--', alpha=0.5)

    # 右轴: Latency
    ax2 = ax1.twinx()
    color_lat = 'tab:red'
    ax2.set_ylabel('Avg Latency (us)', color=color_lat, fontsize=12, fontweight='bold')
    ln2 = ax2.plot(x_labels, y_lat, color=color_lat, marker='^', linestyle=':', linewidth=2, label='Latency')
    ax2.tick_params(axis='y', labelcolor=color_lat)

    # 合并图例
    lines = ln1 + ln_theo + ln2
    labels = [l.get_label() for l in lines]
    ax1.legend(lines, labels, loc='best', frameon=True, shadow=True)

    plt.title(f"{scenario_name} Performance", fontsize=14)
    fig.autofmt_xdate(rotation=45) 
    fig.tight_layout()

    # 保存
    file_path = os.path.join(PLOTS_SAVE_DIR, f"{scenario_name}.png")
    plt.savefig(file_path, dpi=150)
    plt.close()
    print(f"[*] Plot saved to: {file_path}")


# ================= 主程序 =================

if __name__ == "__main__":
    if not os.path.exists(MQSIM_EXEC_PATH):
        print(f"Error: Executable not found at {MQSIM_EXEC_PATH}")
        exit(1)
    if not os.path.exists(HW_CONFIG_PATH):
        print(f"Error: HW Config not found at {HW_CONFIG_PATH}")
        exit(1)

    # === 构建 8 个 Scenario 字典 ===
    
    # 辅助函数：根据大小生成可读的 label
    def get_size_label(bytes_val):
        if bytes_val >= 1024**3: return f"{bytes_val//1024**3}GB"
        if bytes_val >= 1024**2: return f"{bytes_val//1024**2}MB"
        if bytes_val >= 1024: return f"{bytes_val//1024}KB"
        return f"{bytes_val}B"

    # 准备 Size 列表: 8KB (2^13) 到 1GB (2^30)
    size_scan_list = []
    for exp in range(13, 31): # 13..30
        size_scan_list.append(2**exp)

    # 1. STREAMING_READ_SCENARIO_SCAN_QD_8KB
    # Fixed Size 16MB, Scan QD 1..32
    SCENARIO_1 = {
        f"seq_read_size_16MB_{qd}QD": {
            "Read_Percentage": 100,
            "Address_Distribution": "STREAMING",
            "Average_Request_Size": REQ_SIZE_8KB,
            "Average_No_of_Reqs_in_Queue": qd,
            "Total_Requests_To_Generate": FIXED_SIZE_SECTORS_SCAN_QD // REQ_SIZE_8KB
        }
        for qd in list(range(1, 33)) + [48, 64] # 稍微多扫一点点
    }

    # 2. STREAMING_READ_SCENARIO_SCAN_SIZE_8KB
    # Scan Size 8KB -> 1GB, QD = min(48, Total/24chips/8KB) => Total_Reqs / 24
    SCENARIO_2 = {}
    for size_bytes in size_scan_list:
        total_reqs = size_bytes // REQ_SIZE_BYTES_8KB
        # QD logic: min(48, total_size / (24 * 8KB)) -> min(48, total_reqs // 24)
        # 注意: 如果总请求数很少（比如1个），QD至少要是1，否则可能出错
        calculated_qd = min(128, max(1, total_reqs // 24))
        SCENARIO_2[f"seq_read_size_{get_size_label(size_bytes)}_calcQD"] = {
            "Read_Percentage": 100,
            "Address_Distribution": "STREAMING",
            "Average_Request_Size": REQ_SIZE_8KB,
            "Average_No_of_Reqs_in_Queue": calculated_qd,
            "Total_Requests_To_Generate": total_reqs
        }

    # 3. RANDOM_READ_SCENARIO_SCAN_SIZE_8KB
    # Same as 2, Random
    SCENARIO_3 = {}
    for size_bytes in size_scan_list:
        total_reqs = size_bytes // REQ_SIZE_BYTES_8KB
        calculated_qd = min(48, max(1, total_reqs // 24))
        SCENARIO_3[f"rand_read_size_{get_size_label(size_bytes)}_calcQD"] = {
            "Read_Percentage": 100,
            "Address_Distribution": "RANDOM_UNIFORM",
            "Average_Request_Size": REQ_SIZE_8KB,
            "Average_No_of_Reqs_in_Queue": calculated_qd,
            "Total_Requests_To_Generate": total_reqs
        }

    # 4. RANDOM_READ_SCENARIO_SCAN_QD_8KB
    # Fixed 16MB, Random, Scan QD
    SCENARIO_4 = {
        f"rand_read_size_16MB_{qd}QD": {
            "Read_Percentage": 100,
            "Address_Distribution": "RANDOM_UNIFORM",
            "Average_Request_Size": REQ_SIZE_8KB,
            "Average_No_of_Reqs_in_Queue": qd,
            "Total_Requests_To_Generate": FIXED_SIZE_SECTORS_SCAN_QD // REQ_SIZE_8KB
        }
        for qd in list(range(1, 128, 16))
    }

    # 5. STREAMING_WRITE_SCENARIO_SCAN_QD_8KB
    # Fixed 16MB, Streaming Write, Scan QD
    SCENARIO_5 = {
        f"seq_write_size_16MB_{qd}QD": {
            "Read_Percentage": 0,
            "Address_Distribution": "STREAMING",
            "Average_Request_Size": REQ_SIZE_8KB,
            "Average_No_of_Reqs_in_Queue": qd,
            "Total_Requests_To_Generate": FIXED_SIZE_SECTORS_SCAN_QD // REQ_SIZE_8KB
        }
        for qd in list(range(1, 33))
    }

    # 6. RANDOM_WRITE_SCENARIO_SCAN_QD_8KB
    # Fixed 16MB, Random Write, Scan QD
    SCENARIO_6 = {
        f"rand_write_size_16MB_{qd}QD": {
            "Read_Percentage": 0,
            "Address_Distribution": "RANDOM_UNIFORM",
            "Average_Request_Size": REQ_SIZE_8KB,
            "Average_No_of_Reqs_in_Queue": qd,
            "Total_Requests_To_Generate": FIXED_SIZE_SECTORS_SCAN_QD // REQ_SIZE_8KB
        }
        for qd in list(range(1, 33))
    }

    # 7. STREAMING_WRITE_SCENARIO_SCAN_SIZE_8KB
    # Same as 2, Streaming Write
    SCENARIO_7 = {}
    for size_bytes in size_scan_list:
        total_reqs = size_bytes // REQ_SIZE_BYTES_8KB
        calculated_qd = min(48, max(1, total_reqs // 24))
        SCENARIO_7[f"seq_write_size_{get_size_label(size_bytes)}_calcQD"] = {
            "Read_Percentage": 0,
            "Address_Distribution": "STREAMING",
            "Average_Request_Size": REQ_SIZE_8KB,
            "Average_No_of_Reqs_in_Queue": calculated_qd,
            "Total_Requests_To_Generate": total_reqs
        }

    # 8. RANDOM_WRITE_SCENARIO_SCAN_SIZE_8KB
    # Same as 2, Random Write
    SCENARIO_8 = {}
    for size_bytes in size_scan_list:
        total_reqs = size_bytes // REQ_SIZE_BYTES_8KB
        calculated_qd = min(48, max(1, total_reqs // 24))
        SCENARIO_8[f"rand_write_size_{get_size_label(size_bytes)}_calcQD"] = {
            "Read_Percentage": 0,
            "Address_Distribution": "RANDOM_UNIFORM",
            "Average_Request_Size": REQ_SIZE_8KB,
            "Average_No_of_Reqs_in_Queue": calculated_qd,
            "Total_Requests_To_Generate": total_reqs
        }

    # --- 执行列表 ---
    all_scenarios_to_run = [
        ("STREAMING_READ_SCAN_QD_8KB", SCENARIO_1),
        ("STREAMING_READ_SCAN_SIZE_8KB", SCENARIO_2),
        ("RANDOM_READ_SCAN_SIZE_8KB", SCENARIO_3),
        ("RANDOM_READ_SCAN_QD_8KB", SCENARIO_4),
        ("STREAMING_WRITE_SCAN_QD_8KB", SCENARIO_5),
        ("RANDOM_WRITE_SCAN_QD_8KB", SCENARIO_6),
        ("STREAMING_WRITE_SCAN_SIZE_8KB", SCENARIO_7),
        ("RANDOM_WRITE_SCAN_SIZE_8KB", SCENARIO_8),
    ]

    # 初始化 (max_workers 请根据您的CPU核心数调整)
    automator = MQSimAutomator(max_workers=32)

    # 循环执行所有场景组
    for name, scenario_dict in all_scenarios_to_run:
        print(f"\n=== Running Scenario Group: {name} ===")
        
        # 1. 运行仿真
        final_results = automator.run(scenario_dict)
        
        # 2. 画图并保存 (含理论线)
        plot_and_save_results(name, final_results)

