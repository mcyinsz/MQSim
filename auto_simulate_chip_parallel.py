import os
import shutil
import subprocess
import multiprocessing
import xml.etree.ElementTree as ET
import glob
import time
from typing import Dict, List, Any

# ================= 配置区域 =================
# MQSim 可执行文件的路径 (请修改为你的实际路径)
MQSIM_EXEC_PATH = "./MQSim" 
# 硬件配置文件的路径 (请修改为你的实际路径)
HW_CONFIG_PATH = "./HBF_workspace/hbfconfig_chip_parallel.xml" 
# 临时工作目录的根路径，用于存放并行跑的中间文件
TEMP_WORKSPACE_ROOT = "./temp_sim_workspace/temp_symthesis_chip_parallel_sim_workspace"
# ===========================================

def generate_workload_xml(filepath: str, params: Dict[str, Any]):
    """
    根据参数字典生成 Workload XML 文件。
    如果 params 中缺少某些字段，则使用下面的默认值。
    """
    # 默认的长 ID 列表，直接复制自你的示例
    default_chip_ids = "2,3,4,5,6,7,8,9,10,75" #"0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,71,72,73,74,75"
    default_channel_ids = "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31" #"0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31"
    default_die_ids = "0"
    default_plane_ids = "0"

    # 填充数据
    p = {
        "Channel_IDs": params.get("Channel_IDs", default_channel_ids),
        "Chip_IDs": params.get("Chip_IDs", default_chip_ids),
        "Die_IDs": params.get("Die_IDs", default_die_ids),
        "Plane_IDs": params.get("Plane_IDs", default_plane_ids),
        "Initial_Occupancy_Percentage": params.get("Initial_Occupancy_Percentage", 10),
        "Average_No_of_Reqs_in_Queue": params.get("Average_No_of_Reqs_in_Queue", 512),
        "Read_Percentage": params.get("Read_Percentage", 100),
        "Address_Distribution": params.get("Address_Distribution", "STREAMING"),
        "Average_Request_Size": params.get("Average_Request_Size", 256),
        "Total_Requests_To_Generate": params.get("Total_Requests_To_Generate", 51200),
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
    """
    解析 MQSim 生成的结果 XML，提取关键指标。
    """
    try:
        tree = ET.parse(result_path)
        root = tree.getroot()
        
        # 定位到 Host.IO_Flow 节点
        # 注意：根据提供的 XML，路径是 MQSim_Results -> Host -> Host.IO_Flow
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
    """
    单个仿真任务的工作函数。
    args: (case_name, params_dict)
    """
    case_name, params = args
    
    # 1. 创建独立的临时目录，避免文件冲突
    run_dir = os.path.join(TEMP_WORKSPACE_ROOT, case_name)
    if os.path.exists(run_dir):
        shutil.rmtree(run_dir)
    os.makedirs(run_dir)

    try:
        # 获取绝对路径
        abs_exec_path = os.path.abspath(MQSIM_EXEC_PATH)
        abs_hw_config = os.path.abspath(HW_CONFIG_PATH)
        workload_file = os.path.join(run_dir, "workload.xml")

        # 2. 生成 workload 文件
        generate_workload_xml(workload_file, params)

        # 3. 构建命令
        # 注意：MQSim 可能会在当前工作目录下生成结果文件，所以我们改变 cwd
        cmd = [abs_exec_path, "-i", abs_hw_config, "-w", os.path.abspath(workload_file)]

        # 4. 执行仿真
        # 将 stdout 输出到 null 或日志文件以保持控制台整洁
        start_time = time.time()
        with open(os.path.join(run_dir, "sim.log"), "w") as log_file:
            # input=b'\n' 相当于向程序发送了一个回车键
            subprocess.run(
                cmd, 
                cwd=run_dir, 
                stdout=log_file, 
                stderr=subprocess.STDOUT, 
                check=True,
                input=b'\t' 
            )
        duration = time.time() - start_time

        # 5. 查找结果文件
        # MQSim 生成的结果文件通常含有 _scenario_1.xml 后缀，或者我们直接找那个新生成的 xml
        # 排除掉我们自己生成的 workload.xml
        xml_files = glob.glob(os.path.join(run_dir, "*.xml"))
        result_file = None
        for f in xml_files:
            if "workload.xml" not in f and "hbfconfig" not in f:
                result_file = f
                break
        
        if not result_file:
            return case_name, {"Error": "Result XML file not generated"}

        # 6. 解析结果
        metrics = parse_result_xml(result_file)
        metrics["Simulation_Time_Sec"] = duration
        
        return case_name, metrics

    except subprocess.CalledProcessError:
        return case_name, {"Error": "MQSim Execution Failed"}
    except Exception as e:
        return case_name, {"Error": str(e)}
    finally:
        # 可选：运行完后清理临时文件
        # shutil.rmtree(run_dir) 
        pass

class MQSimAutomator:
    def __init__(self, max_workers=None):
        self.max_workers = max_workers
        # 确保根目录存在
        if not os.path.exists(TEMP_WORKSPACE_ROOT):
            os.makedirs(TEMP_WORKSPACE_ROOT)

    def run(self, workload_configs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        入口函数。
        workload_configs: 字典，Key是测试例名称，Value是参数字典
        """
        tasks = []
        for name, params in workload_configs.items():
            tasks.append((name, params))

        print(f"[*] Starting {len(tasks)} simulations using {self.max_workers or 'default'} workers...")
        
        results = {}
        with multiprocessing.Pool(processes=self.max_workers) as pool:
            # 异步执行
            for name, res in pool.map(worker_task, tasks):
                results[name] = res
                print(f"    -> Finished: {name} | IOPS: {res.get('IOPS', 'N/A')}")
        
        print("[*] All simulations completed.")
        return results

# ================= 使用示例 =================

if __name__ == "__main__":
    # 检查路径是否存在
    if not os.path.exists(MQSIM_EXEC_PATH):
        print(f"Error: Executable not found at {MQSIM_EXEC_PATH}")
        exit(1)
    if not os.path.exists(HW_CONFIG_PATH):
        print(f"Error: HW Config not found at {HW_CONFIG_PATH}")
        exit(1)

    STREAMING_READ_SCENARIO_128KBREQ = {
        f"seq_read_total_size_{2**(size_log-10)}MB_128kBreq": {
            "Read_Percentage": 100,
            "Average_Request_Size": 256,  # 1sector = 512B
            "Average_No_of_Reqs_in_Queue": min(int(2**(size_log-7)), 512),
            "Total_Requests_To_Generate": 2**size_log//128,
            "Address_Distribution": "STREAMING",

        }

        for size_log in range(12, 24)
    }

    STREAMING_WRITE_SCENARIO_128KBREQ = {
        f"seq_write_total_size_{2**(size_log-10)}MB_128kBreq": {
            "Read_Percentage": 0,
            "Average_Request_Size": 256,  # 1sector = 512B
            "Average_No_of_Reqs_in_Queue": min(int(int(2**(size_log-7))), 512),
            "Total_Requests_To_Generate": 2**size_log//128,
            "Address_Distribution": "STREAMING",
        }

        for size_log in range(12, 24)
    }

    STREAMING_READ_SCENARIO_8KBREQ = {
        f"seq_read_total_size_{2**(size_log-10)}MB_8kBreq": {
            "Read_Percentage": 100,
            "Average_Request_Size": 16,  # 1sector = 512B
            "Average_No_of_Reqs_in_Queue": min(int(2**(size_log-3)), 1024),
            "Total_Requests_To_Generate": 2**size_log//8,
            "Address_Distribution": "STREAMING", 
        }

        for size_log in range(12, 24)
    }

    RANDOM_READ_SCENARIO_128KBREQ = {
        f"rand_read_total_size_{2**(size_log-10)}MB_128kBreq": {
            "Read_Percentage": 100,
            "Average_Request_Size": 256,  # 1sector = 512B
            "Average_No_of_Reqs_in_Queue": min(int(2**(size_log-7)), 512),
            "Total_Requests_To_Generate": 2**size_log//128,
            "Address_Distribution": "RANDOM_UNIFORM",
        }

        for size_log in range(12, 24)
    }

    RANDOM_READ_SCENARIO_8KBREQ = {
        f"rand_read_total_size_{2**(size_log-10)}MB_8kBreq": {
            "Read_Percentage": 100,
            "Average_Request_Size": 16,  # 1sector = 512B
            "Average_No_of_Reqs_in_Queue": min(int(2**(size_log-3)), 1024),
            "Total_Requests_To_Generate": 2**size_log//8,
            "Address_Distribution": "RANDOM_UNIFORM", 
        }

        for size_log in range(12, 24)
    }

    STREAMING_READ_SCENARIO_SCAN_QD = {
        f"seq_read_total_size_{2**(18-10)}MB_{qd}QD": {
            "Read_Percentage": 100,
            "Average_Request_Size": 512,  # 1sector = 512B
            "Average_No_of_Reqs_in_Queue": qd,
            "Total_Requests_To_Generate": 2**18//256,
            "Address_Distribution": "STREAMING",

        }

        for qd in range(16,17,1)
    }

    STREAMING_READ_SCENARIO_SCAN_QD_LOW_RANGE = {
        f"seq_read_total_size_{2**(18-10)}MB_{qd}QD": {
            "Read_Percentage": 100,
            "Average_Request_Size": 512,  # 1sector = 512B
            "Average_No_of_Reqs_in_Queue": qd,
            "Total_Requests_To_Generate": 2**18//256,
            "Address_Distribution": "STREAMING",

        }

        for qd in range(1, 17)
    }

    STREAMING_READ_SCENARIO_SCAN_QD_SMALL_REQ = {
        f"seq_read_total_size_{2**(18-10)}MB_{qd}QD_small": {
            "Read_Percentage": 100,
            "Average_Request_Size": 16,  # 1sector = 512B
            "Average_No_of_Reqs_in_Queue": qd,
            "Total_Requests_To_Generate": 2**18//8,
            "Address_Distribution": "STREAMING",

        }

        for qd in range(1, 512, 16)
    }


    scenarios = STREAMING_READ_SCENARIO_SCAN_QD

    # 2. 初始化并运行
    # max_workers=4 表示同时跑4个仿真，根据你的CPU核心数调整
    automator = MQSimAutomator(max_workers=96) 
    final_results = automator.run(scenarios)

    # 3. 打印最终汇总结果
    print("\n========= Final Summary =========")
    print(f"{'Case Name':<20} | {'IOPS':<15} | {'Bandwidth (GB/s)':<20} | {'Latency (us)':<15}")
    print("-" * 80)
    for name, data in final_results.items():
        if "Error" in data:
            print(f"{name:<20} | ERROR: {data['Error']}")
        else:
            # Bandwidth 原始单位通常是 bytes/sec，这里转换为 GB/s 用于展示
            bw_gb = data['Bandwidth'] / 1024 / 1024 / 1024
            print(f"{name:<20} | {data['IOPS']:<15.2f} | {bw_gb:<20.4f} | {data['Avg_Response_Time']:<15.2f}")