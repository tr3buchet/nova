# Copyright 2012 Rackspace Hosting
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
"""
Fakes For Rackspace Scheduler tests.
"""
import sys

from nova.scheduler import host_manager
from nova.scheduler import rackspace_host_manager


# Monkey patch some things so I don't have to duplicate a bunch of code
host_manager.HostManager = rackspace_host_manager.RackspaceHostManager

# Unload the fakes module if it was loaded previously.  If it was
# loaded previously, it is not using our monkey patching from above.
try:
    del sys.modules['nova.tests.scheduler.fakes']
except KeyError:
    pass
# Now we can import the core fakes.py into here
from nova.tests.scheduler.fakes import *
