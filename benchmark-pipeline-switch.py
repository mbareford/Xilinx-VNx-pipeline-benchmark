# python benchmark-pipeline-switch.py -s 10.1.212.129 -p 8786 -f 10.1.212.159 10.1.212.160 -g 60512 62177 -x ./vnx_benchmark_if0.xclbin
# python benchmark-pipeline-switch.py -s 10.1.212.127 -p 8786 -f 10.1.212.157 10.1.212.159 10.1.212.160 -g 60512 62177 63842 -x ./vnx_benchmark_if0.xclbin
# python benchmark-pipeline-switch.py -s 10.1.212.126 -p 8786 -f 10.1.212.156 10.1.212.157 10.1.212.159 10.1.212.160 -g 60512 62177 63842 65507 -x ./vnx_benchmark_if0.xclbin

import os
import time
import platform
import argparse

from dask.distributed import Client, get_client

from vnx_utils import *
import pynq
exec(open('../Notebooks/dask_pynq.py').read())


parser = argparse.ArgumentParser()
parser.add_argument('-s','--scheduler-ip', help='Dask scheduler IP address', required=True)
parser.add_argument('-p','--scheduler-port', help='Dask scheduler port number', required=True)
parser.add_argument('-f','--fpga-ip', nargs='+', help='FPGA IP addresses', required=True)
parser.add_argument('-g','--fpga-port', nargs='+', help='FPGA port numbers', required=True)
parser.add_argument('-x','--xcl-bin', help='XCL binary', required=True)
args = parser.parse_args()

print("Scheduler IP: "+args.scheduler_ip)
print("Scheduler Port: "+args.scheduler_port)
print("FPGA IP Addresses: "+str(args.fpga_ip))
print("FPGA Ports: "+str(args.fpga_port))
print("XCL Binary: "+args.xcl_bin)


client = Client("tcp://"+args.scheduler_ip+":"+args.scheduler_port)
print(client)

client_info = client.scheduler_info()['workers']
workers = []
for cli in client_info:
    workers.append(client_info[cli]['name'])

nworkers = len(args.fpga_ip)
if len(workers) != nworkers:
    print("Configure your Dask cluster with "+str(nworkers)+" worker(s).")


def verify_workers():
    node_name = platform.node()
    shell_version = os.popen("xbutil dump | grep dsa_name").read()
    return node_name, shell_version[24:-2]

worker_client = []
worker_check = []
dask_device = []
worker_overlay = []
worker_status = []
for i, w in enumerate(workers):
    worker_client.append(client.submit(verify_workers, workers=w, pure=False))
    worker_check.append(worker_client[-1].result())

    dask_device.append(DaskDevice(client, w))
    worker_overlay.append(pynq.Overlay(args.xcl_bin, device=dask_device[-1]))
    worker_status.append(worker_overlay[-1].networklayer_0.updateIPAddress(args.fpga_ip[i], debug=True))

    print('Worker name: {} | shell version: {}'.format(worker_check[-1][0],worker_check[-1][1]))
    print("Link worker "+str(i)+" {}".format(worker_overlay[-1].cmac_0.linkStatus()))
    print("Worker "+str(i)+" {}".format(worker_status[-1]))


# configure local alveo card
worker_overlay[0].networklayer_0.sockets[12] = (args.fpga_ip[1], args.fpga_port[1], args.fpga_port[0], True)
worker_overlay[0].networklayer_0.populateSocketTable(debug=True)
worker_overlay[0].networklayer_0.arpDiscovery()
#worker_overlay[0].networklayer_0.readARPTable()

# configure remote alveo card(s) apart from the last one (the consumer)
for i, wol in enumerate(worker_overlay[1:-1]):
    j = i+1
    wol.networklayer_0.sockets[3] = (args.fpga_ip[j-1], args.fpga_port[j-1], args.fpga_port[j], True)
    wol.networklayer_0.sockets[12] = (args.fpga_ip[j+1], args.fpga_port[j+1], args.fpga_port[j], True)
    wol.networklayer_0.populateSocketTable(debug=True)
    wol.networklayer_0.arpDiscovery()
    #wol.networklayer_0.readARPTable()

# configure last remote alveo card
worker_overlay[-1].networklayer_0.sockets[3] = (args.fpga_ip[-2], args.fpga_port[-2], args.fpga_port[-1], True)
worker_overlay[-1].networklayer_0.populateSocketTable(debug=True)
worker_overlay[-1].networklayer_0.arpDiscovery()
#worker_overlay[-1].networklayer_0.readARPTable()


# configure application
worker_tg = []
worker_tg.append(worker_overlay[0].traffic_generator_0_3)
worker_tg[-1].register_map.mode = benchmark_mode.index('PRODUCER')
worker_tg[-1].register_map.dest_id = 12

for wol in worker_overlay[1:-1]:
    worker_tg.append(wol.traffic_generator_0_3)
    worker_tg[-1].register_map.debug_reset = 1
    worker_tg[-1].register_map.mode = benchmark_mode.index('LOOPBACK')
    worker_tg[-1].register_map.dest_id = 12
    worker_tg[-1].register_map.CTRL.AP_START = 1

worker_tg.append(worker_overlay[-1].traffic_generator_0_3)
worker_tg[-1].register_map.debug_reset = 1
worker_tg[-1].register_map.mode = benchmark_mode.index('CONSUMER')
worker_tg[-1].register_map.CTRL.AP_START = 1

#freq = int(worker_tg[-1].clock_dict['clock0']['frequency'])
freq = 300
for wtg in worker_tg:
    wtg.freq = freq


experiment_dict = {}
local_dict = {}

#for pkt in [1000000, 1000000000]:
for pkt in [1000000]:

    worker_tg[0].register_map.debug_reset = 1
    worker_tg[0].register_map.time_between_packets = 0
    worker_tg[0].register_map.number_packets = pkt
    
    worker_tg[-1].register_map.debug_reset = 1

    local_dict = {}
    for i in reversed(range(23)):
        beats = i + 1
    
        worker_tg[0].register_map.number_beats = beats
        worker_tg[0].register_map.CTRL.AP_START = 1
        print("waiting for wtg 0 to transmit "+str(pkt)+" packets...")
        while int(worker_tg[0].register_map.out_traffic_packets) != pkt:
            time.sleep(0.8)
        print("done")

        rx_tot_pkt, rx_thr, rx_time = worker_tg[-1].computeThroughputApp('rx')
        tx_tot_pkt, tx_thr, tx_time = worker_tg[0].computeThroughputApp('tx')

        # Create dict entry for this particular experiment
        entry_dict = {'size': (beats * 64), 'rx_pkts' : rx_tot_pkt, 'tx_thr': tx_thr, 'rx_thr': rx_thr}
        local_dict[beats] = entry_dict
        
        # Reset probes to prepare for next computation
        for wtg in worker_tg:
            wtg.resetProbes()
        
        theoretical = (beats * 64 * 100)/((beats*64)+8+20+14+4) 
        print("Sent {:14,} size: {:4}-Byte done!	Got {:14,} took {:8.4f} sec, thr: {:.3f} Gbps, theoretical: {:.3f} Gbps, difference: {:6.3f} Gbps"\
            .format(pkt,beats*64, rx_tot_pkt, rx_time, rx_thr, theoretical, theoretical-rx_thr))
        
        time.sleep(0.5)

    experiment_dict[pkt] = local_dict


print("\n*** experiment_dict ***")
print(experiment_dict)

print("\n\n*** debugProbes ***")
for i, wol in enumerate(worker_overlay):
    print("debugProbe "+str(i))
    print(wol.networklayer_0.getDebugProbes)

print("\n\n*** registerMaps ***")
for i, wtg in enumerate(worker_tg):
    print("registerMap "+str(i))
    print(wtg.register_map)


for wol in worker_overlay:
    pynq.Overlay.free(wol);
