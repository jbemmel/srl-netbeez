#!/usr/bin/env python
# coding=utf-8

import grpc
import datetime
import sys
import logging
import socket
import os
import re
import ipaddress
import json
import signal
import traceback
import subprocess

import sdk_service_pb2
import sdk_service_pb2_grpc
import lldp_service_pb2
import config_service_pb2
import route_service_pb2
import nexthop_group_service_pb2
import sdk_common_pb2

# To report state back
import telemetry_service_pb2
import telemetry_service_pb2_grpc

# See opt/rh/rh-python36/root/usr/lib/python3.6/site-packages/sdk_protos/bfd_service_pb2.py
import bfd_service_pb2

from logging.handlers import RotatingFileHandler

############################################################
## Agent will start with this name
############################################################
agent_name='netbeez-agent'

############################################################
## Open a GRPC channel to connect to sdk_mgr on the dut
## sdk_mgr will be listening on 50053
############################################################
#channel = grpc.insecure_channel('unix:///opt/srlinux/var/run/sr_sdk_service_manager:50053')
channel = grpc.insecure_channel('127.0.0.1:50053')
metadata = [('agent_name', agent_name)]
stub = sdk_service_pb2_grpc.SdkMgrServiceStub(channel)

def SendKeepAlive():
    request = sdk_service_pb2.KeepAliveRequest()
    result = stub.KeepAlive(request=request, metadata=metadata)
    print( f'Status of KeepAlive response :: {result.status}' )

############################################################
## Subscribe to required event
## This proc handles subscription of: Interface, LLDP,
##                      Route, Network Instance, Config
############################################################
def Subscribe(stream_id, option):
    op = sdk_service_pb2.NotificationRegisterRequest.AddSubscription
    if option == 'lldp':
        entry = lldp_service_pb2.LldpNeighborSubscriptionRequest()
        # TODO filter out 'mgmt0' interfaces
        request = sdk_service_pb2.NotificationRegisterRequest(op=op, stream_id=stream_id, lldp_neighbor=entry)
    elif option == 'cfg':
        entry = config_service_pb2.ConfigSubscriptionRequest()
        entry.key.js_path = '.' + agent_name
        request = sdk_service_pb2.NotificationRegisterRequest(op=op, stream_id=stream_id, config=entry)
    elif option == 'bfd':
        entry = bfd_service_pb2.BfdSessionSubscriptionRequest()
        request = sdk_service_pb2.NotificationRegisterRequest(op=op, stream_id=stream_id, bfd_session=entry)
    elif option == 'route':
        # This includes all network namespaces, i.e. including mgmt
        entry = route_service_pb2.IpRouteSubscriptionRequest()
        request = sdk_service_pb2.NotificationRegisterRequest(op=op, stream_id=stream_id, route=entry)
    elif option == 'nexthop_group':
        entry = nexthop_group_service_pb2.NextHopGroupSubscriptionRequest()
        request = sdk_service_pb2.NotificationRegisterRequest(op=op, stream_id=stream_id, nhg=entry)

    subscription_response = stub.NotificationRegister(request=request, metadata=metadata)
    print('Status of subscription response for {}:: {}'.format(option, subscription_response.status))

############################################################
## Subscribe to all the events that Agent needs
############################################################
def Subscribe_Notifications(stream_id):
    '''
    Agent will receive notifications to what is subscribed here.
    '''
    if not stream_id:
        logging.info("Stream ID not sent.")
        return False

    # Subscribe to config changes, first
    Subscribe(stream_id, 'cfg')

    Subscribe(stream_id, 'bfd')  # BFD notifications

    # To map route notifications to BGP peers
    Subscribe(stream_id, 'nexthop_group')

############################################################
## Function to populate state of agent config
## using telemetry -- add/update info from state
############################################################
def Add_Telemetry(js_path, js_data):
    telemetry_stub = telemetry_service_pb2_grpc.SdkMgrTelemetryServiceStub(channel)
    telemetry_update_request = telemetry_service_pb2.TelemetryUpdateRequest()
    telemetry_info = telemetry_update_request.state.add()
    telemetry_info.key.js_path = js_path
    telemetry_info.data.json_content = js_data
    logging.info(f"Telemetry_Update_Request :: {telemetry_update_request}")
    telemetry_response = telemetry_stub.TelemetryAddOrUpdate(request=telemetry_update_request, metadata=metadata)
    return telemetry_response

############################################################
## Function to populate state fields of the agent
## It updates command: info from state auto-config-agent
############################################################
def Update_Peer_State(peer_ip, section, update_data):
    _ip_key = '.'.join([i.zfill(3) for i in peer_ip.split('.')]) # sortable
    js_path = '.' + agent_name + '.peer{.peer_ip=="' + _ip_key + '"}.'+section
    response = Add_Telemetry( js_path=js_path, js_data=json.dumps(update_data) )
    logging.info(f"Telemetry_Update_Response :: {response}")

def Update_Global_State(state, var, val):
    js_path = '.' + agent_name + '.' + var
    response = Add_Telemetry( js_path=js_path, js_data=json.dumps(val) )
    logging.info(f"Telemetry_Update_Response :: {response}")

##################################################################
## Proc to process the config Notifications received by auto_config_agent
## At present processing config from js_path containing agent_name
##################################################################
def Handle_Notification(obj, state):
    if obj.HasField('route'):
        addr = ipaddress.ip_address(obj.route.key.ip_prefix.ip_addr.addr).__str__()
        prefix = obj.route.key.ip_prefix.prefix_length
        nhg_id = obj.route.data.nhg_id
        owner_id = obj.route.data.owner_id # To correlate Delete later on
        nh_ip = "?"
        if nhg_id in state.nhg_map:
           nh_ip = state.nhg_map[nhg_id]
        _op = ""
        if obj.route.op == "Delete":
           _op = "-"
           if owner_id in state.owner_id_map:
             nh_ip = state.owner_id_map[ owner_id ]
             delattr( state.owner_id_map, owner_id )
        elif nh_ip != "?":
           state.owner_id_map[owner_id] = nh_ip # Remember for Delete
        logging.info( f"ROUTE notification: {_op}{addr}/{prefix} nhg={nhg_id} ip={nh_ip} owner_id={owner_id}" )
        if nh_ip != "?":
           Update_RouteFlapcounts(state, nh_ip, f'{_op}{addr}/{prefix}' )

    elif obj.HasField('nhg'):
        try:
           nhg_id = obj.nhg.key
           if (obj.nhg.op == "Delete"):
             logging.info( f"NEXTHOP Delete notification: nhg={nhg_id}" )
             if nhg_id in state.nhg_map:
               peer_ip = state.nhg_map[ nhg_id ]
               Update_RouteFlapcounts(state, peer_ip, f'-nhg{nhg_id}')
               delattr( state.nhg_map, nhg_id )
           else:
             for nh in obj.nhg.data.next_hop:
              if nh.ip_nexthop.addr != "":
               addr = ipaddress.ip_address(nh.ip_nexthop.addr).__str__()
               logging.info( f"NEXTHOP notification: {addr} nhg={nhg_id}" )
               Update_RouteFlapcounts(state, addr, f'+nhg{nhg_id}')
               state.nhg_map[nhg_id] = addr
               break
        except Exception as e: # ip_nexthop not set
           logging.error(f'Exception caught while processing nhg :: {e}')

    elif obj.HasField('config') and obj.config.key.js_path != ".commit.end":
        logging.info(f"GOT CONFIG :: {obj.config.key.js_path}")
        if agent_name in obj.config.key.js_path:
            logging.info(f"Got config for agent, now will handle it :: \n{obj.config}\
                            Operation :: {obj.config.op}\nData :: {obj.config.data.json}")
            if obj.config.op == 2:
                logging.info(f"Delete auto-config-agent cli scenario")
                # if file_name != None:
                #    Update_Result(file_name, action='delete')
                response=stub.AgentUnRegister(request=sdk_service_pb2.AgentRegistrationRequest(), metadata=metadata)
                logging.info( f'Handle_Config: Unregister response:: {response}' )
                state = State() # Reset state, works?
            else:
                json_acceptable_string = obj.config.data.json.replace("'", "\"")
                data = json.loads(json_acceptable_string)
                if 'state' in data:
                    state.state = data['state']
                    logging.info(f"Got state :: {state.state}")

                return not state.state is None

    elif obj.HasField('lldp_neighbor'):
        # Update the config based on LLDP info, if needed
        logging.info(f"process LLDP notification : {obj}")
        my_port = obj.lldp_neighbor.key.interface_name  # ethernet-1/x
        to_port = obj.lldp_neighbor.data.port_id
        peer_sys_name = obj.lldp_neighbor.data.system_name

        # TODO process

    elif obj.HasField('bfd_session'):
        logging.info(f"process BFD notification : {obj}")
        src_ip_addr = obj.bfd_session.key.src_ip_addr.addr
        dst_ip_addr = obj.bfd_session.key.dst_ip_addr.addr

        # Integer, 4=UP
        status = obj.bfd_session.data.status  # data.src_if_id, not always set
        src_ip_str = ipaddress.ip_address(src_ip_addr).__str__()
        dst_ip_str = ipaddress.ip_address(dst_ip_addr).__str__()
        logging.info(f"BFD : src={src_ip_str} dst={dst_ip_str} status={status}")

        # TODO process
    else:
        logging.info(f"Unexpected notification : {obj}")

    # dont subscribe to LLDP now
    return False

##
# Update agent state flapcounts for BFD
##
def Update_BFDFlapcounts(state,peer_ip,status=0):
    if peer_ip not in state.bfd_flaps:
       logging.info(f"BFD : initializing flap state for {peer_ip}")
       state.bfd_flaps[peer_ip] = {}
    now = datetime.datetime.now()
    flaps_this_period, history = Update_Flapcounts(state, now, peer_ip, status,
                                                   state.bfd_flaps,
                                                   state.flap_period_mins)
    state_update = {
      "status" : { "value" : "red" if flaps_this_period > state.flap_threshold or status!=4 else "green" },
      "flaps_last_period" : flaps_this_period,
      "flaps_history" : { "value" : history },
      "last_flap_timestamp" : { "value" : now.strftime("%Y-%m-%d %H:%M:%S") }
    }
    Update_Peer_State( peer_ip, 'bfd', state_update )
    Update_Global_State( state, "total_bfd_flaps_last_period", # Works??
      sum( [len(f) for f in state.bfd_flaps.values()] ) )

##
# Update agent state flapcounts for Route entry
##
def Update_RouteFlapcounts(state,peer_ip,prefix):
    if peer_ip not in state.route_flaps:
       logging.info(f"ROUTE : initializing flap state for {peer_ip}")
       state.route_flaps[peer_ip] = {}
    now = datetime.datetime.now()
    flaps_this_period, history = Update_Flapcounts(state, now, peer_ip, prefix,
                                                   state.route_flaps,
                                                   state.flap_period_mins)
    state_update = {
      "status" : { "value" : "red" if flaps_this_period > state.flap_threshold else "green" },
      "flaps_last_period" : flaps_this_period,
      "flaps_history" : { "value" : history },
      "last_flap_timestamp" : { "value" : now.strftime("%Y-%m-%d %H:%M:%S") }
    }
    Update_Peer_State( peer_ip, 'routes', state_update )
    # Update_Global_State( state )

##
# Update agent state flapcounts
##
def Update_Flapcounts(state,now,peer_ip,status,flapmap,period_mins):
    flaps = flapmap[peer_ip]
    if status != 0:
       flaps[now] = status
    keep_flaps = {}
    keep_history = ""
    start_of_period = now - datetime.timedelta(minutes=period_mins)
    _max = state.max_flaps_history
    for i in sorted(flaps.keys(), reverse=True):
       logging.info(f"BFD : check if {i} is within the last period {start_of_period}")
       if ( i > start_of_period and (_max==0 or _max>len(keep_flaps)) ):
           keep_flaps[i] = flaps[i]
           keep_history += f'{ i.strftime("[%H:%M:%S.%f]") } ~ {flaps[i]},'
       else:
           logging.info(f"flap happened outside monitoring period/max: {i}")
           break
    logging.info(f"BFD : keeping last period of flaps for {peer_ip}:{keep_flaps}")
    flapmap[peer_ip] = keep_flaps
    return len( keep_flaps ), keep_history

##################################################################################################
## This functions get the app_id from idb for a given app_name
##################################################################################################
def get_app_id(app_name):
    logging.info(f'Metadata {metadata} ')
    appId_req = sdk_service_pb2.AppIdRequest(name=app_name)
    app_id_response=stub.GetAppId(request=appId_req, metadata=metadata)
    logging.info(f'app_id_response {app_id_response.status} {app_id_response.id} ')
    return app_id_response.id

###########################
# Invokes gnmic client to update router configuration, via bash script
###########################
def script_update_config(some_param):
    logging.info(f'Calling update script: some_param={some_param}' )
    try:
       script_proc = subprocess.Popen(['/etc/opt/srlinux/appmgr/script-to-be-built.sh',
                                       some_param ],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
       stdoutput, stderroutput = script_proc.communicate()
       logging.info(f'script_update_config result: {stdoutput} err={stderroutput}')
    except Exception as e:
       logging.error(f'Exception caught in script_update_config :: {e}')

class State(object):
    def __init__(self):
        self.state = None       # running or stopped

        # TODO more properties

    def __str__(self):
        return str(self.__class__) + ": " + str(self.__dict__)

##################################################################################################
## This is the main proc where all processing for auto_config_agent starts.
## Agent registration, notification registration, Subscrition to notifications.
## Waits on the subscribed Notifications and once any config is received, handles that config
## If there are critical errors, Unregisters the fib_agent gracefully.
##################################################################################################
def Run():
    sub_stub = sdk_service_pb2_grpc.SdkNotificationServiceStub(channel)

    # optional agent_liveliness=<seconds> to have system kill unresponsive agents
    response = stub.AgentRegister(request=sdk_service_pb2.AgentRegistrationRequest(), metadata=metadata)
    logging.info(f"Registration response : {response.status}")

    app_id = get_app_id(agent_name)
    if not app_id:
        logging.error(f'idb does not have the appId for {agent_name} : {app_id}')
    else:
        logging.info(f'Got appId {app_id} for {agent_name}')

    request=sdk_service_pb2.NotificationRegisterRequest(op=sdk_service_pb2.NotificationRegisterRequest.Create)
    create_subscription_response = stub.NotificationRegister(request=request, metadata=metadata)
    stream_id = create_subscription_response.stream_id
    logging.info(f"Create subscription response received. stream_id : {stream_id}")

    Subscribe_Notifications(stream_id)

    stream_request = sdk_service_pb2.NotificationStreamRequest(stream_id=stream_id)
    stream_response = sub_stub.NotificationStream(stream_request, metadata=metadata)

    state = State()
    count = 1
    lldp_subscribed = False
    try:
        for r in stream_response:
            logging.info(f"Count :: {count}  NOTIFICATION:: \n{r.notification}")
            count += 1
            for obj in r.notification:
                if obj.HasField('config') and obj.config.key.js_path == ".commit.end":
                    logging.info('TO DO -commit.end config')
                else:
                    if Handle_Notification(obj, state) and not lldp_subscribed:
                       Subscribe(stream_id, 'lldp')
                       Subscribe(stream_id, 'route')
                       lldp_subscribed = True

                    # Program router_id only when changed
                    # if state.router_id != old_router_id:
                    #   gnmic(path='/network-instance[name=default]/protocols/bgp/router-id',value=state.router_id)
                    logging.info(f'Updated state: {state}')

    except grpc._channel._Rendezvous as err:
        logging.info(f'GOING TO EXIT NOW: {err}')

    except Exception as e:
        logging.error(f'Exception caught :: {e}')
        #if file_name != None:
        #    Update_Result(file_name, action='delete')
        try:
            response = stub.AgentUnRegister(request=sdk_service_pb2.AgentRegistrationRequest(), metadata=metadata)
            logging.error(f'Run try: Unregister response:: {response}')
        except grpc._channel._Rendezvous as err:
            logging.info(f'GOING TO EXIT NOW: {err}')
            sys.exit()
        return True
    sys.exit()
    return True
############################################################
## Gracefully handle SIGTERM signal
## When called, will unregister Agent and gracefully exit
############################################################
def Exit_Gracefully(signum, frame):
    logging.info("Caught signal :: {}\n will unregister fib_agent".format(signum))
    try:
        response=stub.AgentUnRegister(request=sdk_service_pb2.AgentRegistrationRequest(), metadata=metadata)
        logging.error('try: Unregister response:: {}'.format(response))
        sys.exit()
    except grpc._channel._Rendezvous as err:
        logging.info('GOING TO EXIT NOW: {}'.format(err))
        sys.exit()

##################################################################################################
## Main from where the Agent starts
## Log file is written to: /var/log/srlinux/stdout/<dutName>_fibagent.log
## Signals handled for graceful exit: SIGTERM
##################################################################################################
if __name__ == '__main__':
    # hostname = socket.gethostname()
    stdout_dir = '/var/log/srlinux/stdout' # PyTEnv.SRL_STDOUT_DIR
    signal.signal(signal.SIGTERM, Exit_Gracefully)
    if not os.path.exists(stdout_dir):
        os.makedirs(stdout_dir, exist_ok=True)
    log_filename = '{}/netbeez-agent.log'.format(stdout_dir)
    logging.basicConfig(filename=log_filename, filemode='a',\
                        format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',\
                        datefmt='%H:%M:%S', level=logging.INFO)
    handler = RotatingFileHandler(log_filename, maxBytes=3000000,backupCount=5)
    logging.getLogger().addHandler(handler)
    logging.info("START TIME :: {}".format(datetime.datetime.now()))
    if Run():
        logging.info('Agent unregistered and agent routes withdrawed from dut')
    else:
        logging.info('Should not happen')