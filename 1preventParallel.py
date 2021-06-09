# -*- coding: utf-8 -*-
import os
import time
import json
import uuid
import requests
import urllib3

from dnaplugin import in0, in1, in2, in3, in4, in5, in6, in7, in8, in9
from dnaplugin import out0, out1, out2, out3, out4, out5, out6, out7, out8, out9
from dnaplugin import log
from dnaplugin_imported_script import *

from datetime import datetime


# Creator: Danishi-san
# Date: 14.02.2020
# Version: V2
#
# Wait on tasks with lower id to be completed first based on FIFO. Queue this task if necessary.

# Added Giacomo (idea is from pyFos)
log('############################# Get LocalAutomator Credentials ######################')

import json

raised_error = None

try:
  ws_osenv = os.environ.get('WEB_SERVICE_CONNECTIONS', '')
  #log("WS_OSENV: {}".format(str(ws_osenv)))
  ws_conns = json.loads(ws_osenv) if ws_osenv != "" else []
  #log("WS_Conns: {}".format(str(ws_conns) )) 
  if len(ws_conns) != 1:
    raise Exception('[Severe] Internal Error: Web Service Connection not specified. Please add it! Category: OpsCenterAutomator Name: LocalAutomator')

  ws_conn = ws_conns[0]

  username = ws_conn.get('userID')
  password = ws_conn.get('password')
  ip_addr = ws_conn.get('ipAddress')
  port = ws_conn.get('port')
  ip_port = ip_addr + ":" + str(port)
  protocol = ws_conn.get('protocol')

except Exception as e:
  raised_error = e

if isinstance(raised_error, Exception):
  out9('Operation failed with an error. (Details: {})'.format(str(raised_error)))
  raise raised_error

log('#################################### S T A R T ####################################')

#Giacomo: supress ssl certificate verification, not enough to set "verify=False" during request 20.02.2020/14:33
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

thisTaskId = in0
thisServiceName = in2

log("Task Id: " + thisTaskId)
log("Url: " + in1)

waitSec = 15

#Giacomo: in1 is not needed anymore
# in1 = https://10.70.4.109:22016/Automation/launcher/TaskDetails?task_id=3520225

#Giacomo Create baseurl from  retrieved data, to match the connection settings/credentials from HAD Administrion TAB
#OLD: baseUrl = in1[:in1.rfind(":") + 1] + "22016/Automation/v1/"
baseUrl = "{}://{}:{}/Automation/v1/".format(protocol,ip_addr,port)

log("BaseURL: " + baseUrl)

try:
  #Get ServiceID and ServiceName
  r2 = requests.get(baseUrl + 'objects/Tasks/' + str(thisTaskId), headers={'Accept': 'application/json'}, auth=(username, password), verify=False)
  taskProperties = r2.json()
  thisServiceId = str(taskProperties['serviceID'])
except Exception as e:
  raised_error = e
  log("Locking - Retrieve Tasks Error: {}".format(r2))
  # Request to get tasks list failed return an empty list
if isinstance(raised_error, Exception):
  out9('Operation failed to get Maintenance status with an error. (Details: {})'.format(str(r2)))
  raise raised_error


startDatetime = datetime.now()
needtowait = True
serviceIsInMaintenance = True

### Giacomo V1.0.31 PHASE 1 - Initial check when service is started
### 
### 1. Immediately fail if service is in maintenance status
### 2. Immediately fail if there are more then 5 tasks of the same service running

try:
  #Get Service Status
  r3 = requests.get(baseUrl + 'objects/Services/' + thisServiceId, headers={'Accept': 'application/json'}, auth=(username, password), verify=False)
  serviceProperties = r3.json()
except Exception as e:
  raised_error = e
  log("Locking - Retrieve Service Error: {}".format(r3))
  # Request to get service properties list failed return an empty list     

if isinstance(raised_error, Exception):
  out9('Operation failed to get Maintenance status with an error. (Details: {})'.format(str(r3)))
  raise raised_error

#Check if service is in mainatenance
if serviceProperties['serviceState'] == "maintenance" :
  log('DEBUG serviceProperties: {}'.format(str(serviceProperties)))
  log('Mein Service ist in Maintenance.')
  out9('[Severe] This service is in maintenance status. I have to stop now. No deployment/changes done. HPOO will resume this task if needed.')
  raise Exception('[Severe] This service is in maintenance status. I have to stop now. No deployment/changes done. HPOO will resume this task if needed.')
else:
  serviceIsInMaintenance = False
  log('DEBUG serviceProperties: {}'.format(str(serviceProperties)))
  log('Mein Service ist NICHT in Maintenance.')
  out9('')

try:
  r = requests.get(baseUrl + 'objects/Tasks', headers={'Accept': 'application/json'}, auth=(username, password), verify=False)
  data = r.json()['data']
except Exception as e:
  raised_error = e
  log("Locking - Retrieve Tasks Error: {}".format(r))
  # Giacomo
  # Request to get tasks list failed return an empty list
if isinstance(raised_error, Exception):
  out9('Operation failed with an error. (Details: {})'.format(str(r)))
  raise raised_error

# this will never happen because we are a task too!!
if len(data) == 0:
  log("There are no tasks")
else:
  # count how many tasks of the same service are running/waiting to run
  countTasks = 0
  for task in data:
    #log('Task Id: ' + str(task['instanceID']) + ', Service Name: ' + task['serviceName'] + ', Submit Time: ' + task['submitTime'] + ', Status: ' + task['status'])
    #only check tasks of the same service
    if str(task['serviceName']) == str(thisServiceName) :
        #only count tasks that are not completed or failed
        if task['status'] != 'completed' and task['status'] != 'failed' :
            countTasks = countTasks + 1
  if (countTasks > 5):
    #we need to terminate as HAD can have a deadlock if more then 10 tasks are running in parallel
    log('Es sind mehr als 5 Tasks für den selben Service am laufen/warten! Es wurde nichts erstellt. Ich muss jetzt stoppen und HPOO startet mich später wieder.')
    out9('[Severe] There are more than 5 tasks of the same service running in parallel. I have to stop now. No deployment/changes done. HPOO will resume this task later.')
    raise Exception('[Severe] There are more than 5 tasks of the same service running in parallel. I have to stop now. No deployment/changes done. HPOO will resume this task later.')
  
#we can move forward to the next step and wait in the loop to be run
log('Es sind weniger als 5 aktive Tasks. Die 1. Hürde ist geschaft nach ' + str(datetime.now() - startDatetime));


### Giacomo V1.0.30 PHASE 2 - LOOP to wait until I'm allowed to start
###
### 1. Immediately fail if service was set in maintenance status while I was waiting to start
### 2. Wait until it's my turn (no other task of the same service is running and mine is the lowest taskId)
while needtowait == True :
  #Giacomo: v.1.0.30 Service in Maintenance, wait, if maintenance mode is set only 1 task needs to be awaitet to complete, 
  #the other will be waiting in the queue. This was a request by customer operations team, to speed up a manual task activity
  #Remarks: Task Properties contain a serviceState property, but this one is NOT updated when task is running and service state is switched.
  #         We need to go the extra round to get service status -> one more IP Request
  try:
    #Get Service Status
    r3 = requests.get(baseUrl + 'objects/Services/' + thisServiceId, headers={'Accept': 'application/json'}, auth=(username, password), verify=False)
    serviceProperties = r3.json()
  except Exception as e:
    raised_error = e
    log("Locking - Retrieve Service Error: {}".format(r3))
    # Request to get service properties list failed return an empty list     
  if isinstance(raised_error, Exception):
    out9('Operation failed to get Maintenance status with an error. (Details: {})'.format(str(r3)))
    raise raised_error
  #Check if service is in mainatenance
  if serviceProperties['serviceState'] == "maintenance" :
    serviceIsInMaintenance = True
    log('DEBUG serviceProperties: {}'.format(str(serviceProperties)))
    log('Mein Service ist in Maintenance.')
    out9('[Severe] This service changed to maintenance status. I have to stop now. No deployment/changes done. HPOO will resume this task if needed.')
    raise Exception('[Severe] This service changed to maintenance status. I have to stop now. No deployment/changes done. HPOO will resume this task if needed.')
    #log('2 Minuten zusatz Strafe weil ich trotzdem starten wollte. Ich muss länger warten.')
    #time.sleep(120)
  else:
    serviceIsInMaintenance = False
    log('DEBUG serviceProperties: {}'.format(str(serviceProperties)))
    log('Mein Service ist NICHT in Maintenance.')
    out9('')
  
  if serviceIsInMaintenance == False : 
    # Giacomo Updated to use retrieved user/password and dynamicly adapt to HTTP/HTTPS)
    # OLD: r = requests.get(baseUrl + 'objects/Tasks', headers={'Accept': 'application/json'}, auth=('system', 'manager'), verify=False)
    #
    try:
      r = requests.get(baseUrl + 'objects/Tasks', headers={'Accept': 'application/json'}, auth=(username, password), verify=False)
      data = r.json()['data']
    except Exception as e:
      raised_error = e
      log("Locking - Retrieve Tasks Error: {}".format(r))
      # Giacomo
      # Request to get tasks list failed return an empty list
    if isinstance(raised_error, Exception):
      out9('Operation failed with an error. (Details: {})'.format(str(r)))
      raise raised_error
    
    # We set needtowait = False to more easily assign the proper value within the next for-loop.
    needtowait = False

    # this will never happen because we are a task too!!
    if len(data) == 0:
      log("There are no tasks")
      needtowait = False

    else:
      # if we find a task with a lower task id and this task is still not finished we keep on waiting.
      for task in data:
        log('Task Id: ' + str(task['instanceID']) + ', Service Name: ' + task['serviceName'] + ', Submit Time: ' + task['submitTime'] + ', Status: ' + task['status'])
        #only check tasks of the same service
        if str(task['serviceName']) == str(thisServiceName) :
          if task['instanceID'] != int(thisTaskId) :
            if task['status'] != 'completed' and task['status'] != 'failed' :
              if task['instanceID'] < int(thisTaskId) :
                log('Ich muss warten.')
                needtowait = True
              else:
                log('Alter vor Schönheit.')
            else:
              log('Dieser Status interessiert mich nicht.')
          else:
            log('Das bin ich selbst. Wolltest mich testen.')
        else:
          log('Der Task gehört zu einem anderen Service. Der nächste bitte.')

      # Let's wait a certain amount of time before going through the list of tasks again.
    if needtowait == True:      
      log('Ich warte ' + str(waitSec) + ' Sekunden bis ich nochmal alle Tasks abfrage.')
      time.sleep(waitSec)

      
# huff, finished waiting
log('Ich darf endlich loslegen nach ' + str(datetime.now() - startDatetime));

# this is just development stuff
# out0(data)


log('###################################### E N D ######################################')