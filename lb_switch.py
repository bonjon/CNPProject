# Implementation of a simple network of switches, based on simple_switch_13.py
# Basic interaction between controller and switches should be handled by this script

from threading import Thread

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet, arp, icmp, ipv4
from ryu.lib.packet import ether_types

from flow_reroute_app import flow_reroute_app
from newtwork_graph import NetworkGraph

class BaseSwitch(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(BaseSwitch, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.dpids = {}
        self.c = 0

    # We set here an event to handle the first message that the switches will send when they connect to the controller.
    # When a switch connects, we also save its associated datapath object and identifier, so that we can later
    # send any kind of messages to it without waiting for PACKET_IN messages.
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):

        # Obtain the datapath object and save it
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        self.dpids[datapath.id] = datapath

        # Instantiate and send the default MISS rule for the switch (EMPTY match, so it matches with any flow)
        # so that when no flow rule for a packet is found, the packet is sent to the controller.
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    # Function used wrap the FlowMod rule with the ADD command, and then send it to the given datapath.
    def add_flow(self, datapath, priority, match, actions, buffer_id=None):

        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, match=match,
                                    instructions=inst)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    match=match, instructions=inst)
        datapath.send_msg(mod)

    # Function used to delete flows on the given datapath with the given match.
    # If no match is given, all the flows of the given datapath are deleted.
    # Note that we only delete values on table with id 0. If you need to work with multiple tables in yor application
    # it may be needed to change how this function works.
    def delete_flows(self, datapath, out_port, match=False):

        print('Delete Flows')
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # If no match given, delete all rules by default. An EMPTY Match matches with all rules, unless you are using
        # a STRICT version of a given command.
        if not match:
            match = parser.OFPMatch()

        mod = parser.OFPFlowMod(datapath=datapath, command=ofproto.OFPFC_DELETE, match=match, cookie=0,
                                out_port=ofproto.OFPP_ANY, out_group=ofproto.OFPG_ANY, table_id=ofproto.OFPTT_ALL)
        print('Deleted with this out port -> ' + str(out_port))
        datapath.send_msg(mod)

    # Function used to delete a single rule with a source and a destination.
    # It's just a wrapper for the delete_flows function, with some values already set
    def delete_rule(self, datapath, src, dst, out_port):

        parser = datapath.ofproto_parser
        match = parser.OFPMatch(eth_src=src, eth_dst=dst)

        self.delete_flows(datapath, out_port, match=match)

    # We set here the main function which will handle most of the packets that the switches receive.
    # What we do in this case, is, with the use of NetworkGraph, to try and find a route that the packets have to
    # take without using a FLOOD mechanism, which may not work in networks with loops.
    # Note that it is required for hosts
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        
        print(self.c)
        msg = ev.msg
        datapath = msg.datapath
        dpid = datapath.id
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        arp_prt = pkt.get_protocol(arp.arp)

        network = NetworkGraph()

        src_mac = eth.src
        dst_mac = eth.dst
        
        # ignore IPV6 and LLDP packet
        if eth.ethertype == ether_types.ETH_TYPE_LLDP or eth.ethertype == ether_types.ETH_TYPE_IPV6:
            return

        # If the destination mac is not known, but only the IP, we try to find the correct destination by looking
        # at the NetworkGraph of our network.
        # Note that hosts may be required to start a ping before appearing in our NetworkGraph, due to the way Ryu's
        # topology_API is written.
        if dst_mac == 'ff:ff:ff:ff:ff:ff' or dst_mac == '00:00:00:00:00:00':
            if not arp_prt:
                print("Packet couldn't be routed. Dropping...")
                return

            arp_dst = arp_prt.dst_ip
            arp_src = arp_prt.src_ip

            src_mac = network.get_host_by_ip(arp_src)
            dst_mac = network.get_host_by_ip(arp_dst)

            if not dst_mac:
                print("One host tried to reach another host which has not entered the network yet. Dropping packet...")
                print("Try to ping from the destination host before!")
                return
        # We obtain the available paths here
        paths = network.get_all_paths_with_ports(src_mac, dst_mac)
        if len(paths) == 0:
            return
        if self.c % 2 == 0:
            path = paths[0]
        else:
            path = paths[1]
        print('Path is -> ' + str(path))
        # And we install the rule here of all the paths.
        # Uncomment this line if you want the rule to be installed directly when a PACKET_IN is received.
        # Otherwise, find your ideal solution by working on the FlowRouteApp code.
        self.inst_path_rule(paths[0])
        self.inst_path_rule(paths[1])

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        # Find the next output port for the packet to travel to the destination, and send the packet to it.
        out_port = self.next_port(path, dpid)

        actions = [parser.OFPActionOutput(out_port)]
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)
        if self.c % 2 == 0:
            self.del_path_rule(paths[0])
        else:
            self.del_path_rule(paths[1])
        self.c += 1
         
    # Simple function which, given a path with ports and a datapath, returns the port which that datapath has to use
    # as an output, to allow the packet to travel through the given path.
    def next_port(self, path, dpid):

        for n in range(1, len(path) - 1):
            node, out_port = path[n]

            if node == dpid:
                return out_port

    # Function used to install, in one single action, all the flow rules on all the datapaths on a path, in order for
    # packets to travel through that path
    def inst_path_rule(self, path):

        for n in range(1, len(path) - 1):
            node, out_port = path[n]

            datapath = self.dpids[node]
            parser = datapath.ofproto_parser
            match = parser.OFPMatch(eth_dst=path[-1], eth_src=path[0])
            actions = [parser.OFPActionOutput(out_port)]

            self.add_flow(datapath, 1, match, actions)

    # Function used to delete all the rules associated with a given path.
    def del_path_rule(self, path):

        src, dst = (path[0], path[-1])

        for n in range(1, len(path) - 1):
            node, out_port = path[n]
            datapath = self.dpids[node]
            self.delete_rule(datapath, src, dst, out_port)

