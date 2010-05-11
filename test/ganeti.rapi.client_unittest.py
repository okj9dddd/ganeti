#!/usr/bin/python
#

# Copyright (C) 2010 Google Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301, USA.


"""Script for unittesting the RAPI client module"""


import re
import unittest
import warnings

from ganeti import http
from ganeti import serializer

from ganeti.rapi import connector
from ganeti.rapi import rlib2
from ganeti.rapi import client

import testutils


_URI_RE = re.compile(r"https://(?P<host>.*):(?P<port>\d+)(?P<path>/.*)")


def _GetPathFromUri(uri):
  """Gets the path and query from a URI.

  """
  match = _URI_RE.match(uri)
  if match:
    return match.groupdict()["path"]
  else:
    return None


class HttpResponseMock:
  """Dumb mock of httplib.HTTPResponse.

  """

  def __init__(self, code, data):
    self.code = code
    self._data = data

  def read(self):
    return self._data


class OpenerDirectorMock:
  """Mock for urllib.OpenerDirector.

  """

  def __init__(self, rapi):
    self._rapi = rapi
    self.last_request = None

  def open(self, req):
    self.last_request = req

    path = _GetPathFromUri(req.get_full_url())
    code, resp_body = self._rapi.FetchResponse(path, req.get_method())
    return HttpResponseMock(code, resp_body)


class RapiMock(object):
  def __init__(self):
    self._mapper = connector.Mapper()
    self._responses = []
    self._last_handler = None

  def AddResponse(self, response, code=200):
    self._responses.insert(0, (code, response))

  def GetLastHandler(self):
    return self._last_handler

  def FetchResponse(self, path, method):
    try:
      HandlerClass, items, args = self._mapper.getController(path)
      self._last_handler = HandlerClass(items, args, None)
      if not hasattr(self._last_handler, method.upper()):
        raise http.HttpNotImplemented(message="Method not implemented")

    except http.HttpException, ex:
      code = ex.code
      response = ex.message
    else:
      if not self._responses:
        raise Exception("No responses")

      (code, response) = self._responses.pop()

    return code, response


class RapiMockTest(unittest.TestCase):
  def test(self):
    rapi = RapiMock()
    path = "/version"
    self.assertEqual((404, None), rapi.FetchResponse("/foo", "GET"))
    self.assertEqual((501, "Method not implemented"),
                     rapi.FetchResponse("/version", "POST"))
    rapi.AddResponse("2")
    code, response = rapi.FetchResponse("/version", "GET")
    self.assertEqual(200, code)
    self.assertEqual("2", response)
    self.failUnless(isinstance(rapi.GetLastHandler(), rlib2.R_version))


class GanetiRapiClientTests(testutils.GanetiTestCase):
  def setUp(self):
    testutils.GanetiTestCase.setUp(self)

    self.rapi = RapiMock()
    self.http = OpenerDirectorMock(self.rapi)
    self.client = client.GanetiRapiClient('master.foo.com')
    self.client._http = self.http
    # Hard-code the version for easier testing.
    self.client._version = 2

  def assertHandler(self, handler_cls):
    self.failUnless(isinstance(self.rapi.GetLastHandler(), handler_cls))

  def assertQuery(self, key, value):
    self.assertEqual(value, self.rapi.GetLastHandler().queryargs.get(key, None))

  def assertItems(self, items):
    self.assertEqual(items, self.rapi.GetLastHandler().items)

  def assertBulk(self):
    self.assertTrue(self.rapi.GetLastHandler().useBulk())

  def assertDryRun(self):
    self.assertTrue(self.rapi.GetLastHandler().dryRun())

  def testEncodeQuery(self):
    query = [
      ("a", None),
      ("b", 1),
      ("c", 2),
      ("d", "Foo"),
      ("e", True),
      ]

    expected = [
      ("a", ""),
      ("b", 1),
      ("c", 2),
      ("d", "Foo"),
      ("e", 1),
      ]

    self.assertEqualValues(self.client._EncodeQuery(query),
                           expected)

    # invalid types
    for i in [[1, 2, 3], {"moo": "boo"}, (1, 2, 3)]:
      self.assertRaises(ValueError, self.client._EncodeQuery, [("x", i)])

  def testHttpError(self):
    self.rapi.AddResponse(None, code=404)
    try:
      self.client.GetJobStatus(15140)
    except client.GanetiApiError, err:
      self.assertEqual(err.code, 404)
    else:
      self.fail("Didn't raise exception")

  def testGetVersion(self):
    self.client._version = None
    self.rapi.AddResponse("2")
    self.assertEqual(2, self.client.GetVersion())
    self.assertHandler(rlib2.R_version)

  def testGetOperatingSystems(self):
    self.rapi.AddResponse("[\"beos\"]")
    self.assertEqual(["beos"], self.client.GetOperatingSystems())
    self.assertHandler(rlib2.R_2_os)

  def testGetClusterTags(self):
    self.rapi.AddResponse("[\"tag\"]")
    self.assertEqual(["tag"], self.client.GetClusterTags())
    self.assertHandler(rlib2.R_2_tags)

  def testAddClusterTags(self):
    self.rapi.AddResponse("1234")
    self.assertEqual(1234,
        self.client.AddClusterTags(["awesome"], dry_run=True))
    self.assertHandler(rlib2.R_2_tags)
    self.assertDryRun()
    self.assertQuery("tag", ["awesome"])

  def testDeleteClusterTags(self):
    self.rapi.AddResponse("5107")
    self.assertEqual(5107, self.client.DeleteClusterTags(["awesome"],
                                                         dry_run=True))
    self.assertHandler(rlib2.R_2_tags)
    self.assertDryRun()
    self.assertQuery("tag", ["awesome"])

  def testGetInfo(self):
    self.rapi.AddResponse("{}")
    self.assertEqual({}, self.client.GetInfo())
    self.assertHandler(rlib2.R_2_info)

  def testGetInstances(self):
    self.rapi.AddResponse("[]")
    self.assertEqual([], self.client.GetInstances(bulk=True))
    self.assertHandler(rlib2.R_2_instances)
    self.assertBulk()

  def testGetInstanceInfo(self):
    self.rapi.AddResponse("[]")
    self.assertEqual([], self.client.GetInstanceInfo("instance"))
    self.assertHandler(rlib2.R_2_instances_name)
    self.assertItems(["instance"])

  def testCreateInstance(self):
    self.rapi.AddResponse("1234")
    self.assertEqual(1234, self.client.CreateInstance(dry_run=True))
    self.assertHandler(rlib2.R_2_instances)
    self.assertDryRun()

  def testDeleteInstance(self):
    self.rapi.AddResponse("1234")
    self.assertEqual(1234, self.client.DeleteInstance("instance", dry_run=True))
    self.assertHandler(rlib2.R_2_instances_name)
    self.assertItems(["instance"])
    self.assertDryRun()

  def testGetInstanceTags(self):
    self.rapi.AddResponse("[]")
    self.assertEqual([], self.client.GetInstanceTags("fooinstance"))
    self.assertHandler(rlib2.R_2_instances_name_tags)
    self.assertItems(["fooinstance"])

  def testAddInstanceTags(self):
    self.rapi.AddResponse("1234")
    self.assertEqual(1234,
        self.client.AddInstanceTags("fooinstance", ["awesome"], dry_run=True))
    self.assertHandler(rlib2.R_2_instances_name_tags)
    self.assertItems(["fooinstance"])
    self.assertDryRun()
    self.assertQuery("tag", ["awesome"])

  def testDeleteInstanceTags(self):
    self.rapi.AddResponse("25826")
    self.assertEqual(25826, self.client.DeleteInstanceTags("foo", ["awesome"],
                                                           dry_run=True))
    self.assertHandler(rlib2.R_2_instances_name_tags)
    self.assertItems(["foo"])
    self.assertDryRun()
    self.assertQuery("tag", ["awesome"])

  def testRebootInstance(self):
    self.rapi.AddResponse("6146")
    job_id = self.client.RebootInstance("i-bar", reboot_type="hard",
                                        ignore_secondaries=True, dry_run=True)
    self.assertEqual(6146, job_id)
    self.assertHandler(rlib2.R_2_instances_name_reboot)
    self.assertItems(["i-bar"])
    self.assertDryRun()
    self.assertQuery("type", ["hard"])
    self.assertQuery("ignore_secondaries", ["1"])

  def testShutdownInstance(self):
    self.rapi.AddResponse("1487")
    self.assertEqual(1487, self.client.ShutdownInstance("foo-instance",
                                                        dry_run=True))
    self.assertHandler(rlib2.R_2_instances_name_shutdown)
    self.assertItems(["foo-instance"])
    self.assertDryRun()

  def testStartupInstance(self):
    self.rapi.AddResponse("27149")
    self.assertEqual(27149, self.client.StartupInstance("bar-instance",
                                                        dry_run=True))
    self.assertHandler(rlib2.R_2_instances_name_startup)
    self.assertItems(["bar-instance"])
    self.assertDryRun()

  def testReinstallInstance(self):
    self.rapi.AddResponse("19119")
    self.assertEqual(19119, self.client.ReinstallInstance("baz-instance", "DOS",
                                                          no_startup=True))
    self.assertHandler(rlib2.R_2_instances_name_reinstall)
    self.assertItems(["baz-instance"])
    self.assertQuery("os", ["DOS"])
    self.assertQuery("nostartup", ["1"])

  def testReplaceInstanceDisks(self):
    self.rapi.AddResponse("999")
    job_id = self.client.ReplaceInstanceDisks("instance-name",
        ["hda", "hdc"], dry_run=True, iallocator="hail")
    self.assertEqual(999, job_id)
    self.assertHandler(rlib2.R_2_instances_name_replace_disks)
    self.assertItems(["instance-name"])
    self.assertQuery("disks", ["hda,hdc"])
    self.assertQuery("mode", ["replace_auto"])
    self.assertQuery("iallocator", ["hail"])
    self.assertDryRun()

    self.assertRaises(client.InvalidReplacementMode,
                      self.client.ReplaceInstanceDisks,
                      "instance_a", ["hda"], mode="invalid_mode")
    self.assertRaises(client.GanetiApiError,
                      self.client.ReplaceInstanceDisks,
                      "instance-foo", ["hda"], mode="replace_on_secondary")

    self.rapi.AddResponse("1000")
    job_id = self.client.ReplaceInstanceDisks("instance-bar",
        ["hda"], mode="replace_on_secondary", remote_node="foo-node",
        dry_run=True)
    self.assertEqual(1000, job_id)
    self.assertItems(["instance-bar"])
    self.assertQuery("disks", ["hda"])
    self.assertQuery("remote_node", ["foo-node"])
    self.assertDryRun()

  def testGetJobs(self):
    self.rapi.AddResponse('[ { "id": "123", "uri": "\\/2\\/jobs\\/123" },'
                          '  { "id": "124", "uri": "\\/2\\/jobs\\/124" } ]')
    self.assertEqual([123, 124], self.client.GetJobs())
    self.assertHandler(rlib2.R_2_jobs)

  def testGetJobStatus(self):
    self.rapi.AddResponse("{\"foo\": \"bar\"}")
    self.assertEqual({"foo": "bar"}, self.client.GetJobStatus(1234))
    self.assertHandler(rlib2.R_2_jobs_id)
    self.assertItems(["1234"])

  def testWaitForJobChange(self):
    fields = ["id", "summary"]
    expected = {
      "job_info": [123, "something"],
      "log_entries": [],
      }

    self.rapi.AddResponse(serializer.DumpJson(expected))
    result = self.client.WaitForJobChange(123, fields, [], -1)
    self.assertEqualValues(expected, result)
    self.assertHandler(rlib2.R_2_jobs_id_wait)
    self.assertItems(["123"])

  def testCancelJob(self):
    self.rapi.AddResponse("[true, \"Job 123 will be canceled\"]")
    self.assertEqual([True, "Job 123 will be canceled"],
                     self.client.CancelJob(999, dry_run=True))
    self.assertHandler(rlib2.R_2_jobs_id)
    self.assertItems(["999"])
    self.assertDryRun()

  def testGetNodes(self):
    self.rapi.AddResponse("[ { \"id\": \"node1\", \"uri\": \"uri1\" },"
                          " { \"id\": \"node2\", \"uri\": \"uri2\" } ]")
    self.assertEqual(["node1", "node2"], self.client.GetNodes())
    self.assertHandler(rlib2.R_2_nodes)

    self.rapi.AddResponse("[ { \"id\": \"node1\", \"uri\": \"uri1\" },"
                          " { \"id\": \"node2\", \"uri\": \"uri2\" } ]")
    self.assertEqual([{"id": "node1", "uri": "uri1"},
                      {"id": "node2", "uri": "uri2"}],
                     self.client.GetNodes(bulk=True))
    self.assertHandler(rlib2.R_2_nodes)
    self.assertBulk()

  def testGetNodeInfo(self):
    self.rapi.AddResponse("{}")
    self.assertEqual({}, self.client.GetNodeInfo("node-foo"))
    self.assertHandler(rlib2.R_2_nodes_name)
    self.assertItems(["node-foo"])

  def testEvacuateNode(self):
    self.rapi.AddResponse("9876")
    job_id = self.client.EvacuateNode("node-1", remote_node="node-2")
    self.assertEqual(9876, job_id)
    self.assertHandler(rlib2.R_2_nodes_name_evacuate)
    self.assertItems(["node-1"])
    self.assertQuery("remote_node", ["node-2"])

    self.rapi.AddResponse("8888")
    job_id = self.client.EvacuateNode("node-3", iallocator="hail", dry_run=True)
    self.assertEqual(8888, job_id)
    self.assertItems(["node-3"])
    self.assertQuery("iallocator", ["hail"])
    self.assertDryRun()

    self.assertRaises(client.GanetiApiError,
                      self.client.EvacuateNode,
                      "node-4", iallocator="hail", remote_node="node-5")

  def testMigrateNode(self):
    self.rapi.AddResponse("1111")
    self.assertEqual(1111, self.client.MigrateNode("node-a", dry_run=True))
    self.assertHandler(rlib2.R_2_nodes_name_migrate)
    self.assertItems(["node-a"])
    self.assertQuery("live", ["1"])
    self.assertDryRun()

  def testGetNodeRole(self):
    self.rapi.AddResponse("\"master\"")
    self.assertEqual("master", self.client.GetNodeRole("node-a"))
    self.assertHandler(rlib2.R_2_nodes_name_role)
    self.assertItems(["node-a"])

  def testSetNodeRole(self):
    self.rapi.AddResponse("789")
    self.assertEqual(789,
        self.client.SetNodeRole("node-foo", "master-candidate", force=True))
    self.assertHandler(rlib2.R_2_nodes_name_role)
    self.assertItems(["node-foo"])
    self.assertQuery("force", ["1"])
    self.assertEqual("\"master-candidate\"", self.http.last_request.data)

    self.assertRaises(client.InvalidNodeRole,
                      self.client.SetNodeRole, "node-bar", "fake-role")

  def testGetNodeStorageUnits(self):
    self.rapi.AddResponse("42")
    self.assertEqual(42,
        self.client.GetNodeStorageUnits("node-x", "lvm-pv", "fields"))
    self.assertHandler(rlib2.R_2_nodes_name_storage)
    self.assertItems(["node-x"])
    self.assertQuery("storage_type", ["lvm-pv"])
    self.assertQuery("output_fields", ["fields"])

  def testModifyNodeStorageUnits(self):
    self.rapi.AddResponse("14")
    self.assertEqual(14,
        self.client.ModifyNodeStorageUnits("node-z", "lvm-pv", "hda"))
    self.assertHandler(rlib2.R_2_nodes_name_storage_modify)
    self.assertItems(["node-z"])
    self.assertQuery("storage_type", ["lvm-pv"])
    self.assertQuery("name", ["hda"])

  def testRepairNodeStorageUnits(self):
    self.rapi.AddResponse("99")
    self.assertEqual(99, self.client.RepairNodeStorageUnits("node-z", "lvm-pv",
                                                            "hda"))
    self.assertHandler(rlib2.R_2_nodes_name_storage_repair)
    self.assertItems(["node-z"])
    self.assertQuery("storage_type", ["lvm-pv"])
    self.assertQuery("name", ["hda"])

  def testGetNodeTags(self):
    self.rapi.AddResponse("[\"fry\", \"bender\"]")
    self.assertEqual(["fry", "bender"], self.client.GetNodeTags("node-k"))
    self.assertHandler(rlib2.R_2_nodes_name_tags)
    self.assertItems(["node-k"])

  def testAddNodeTags(self):
    self.rapi.AddResponse("1234")
    self.assertEqual(1234,
        self.client.AddNodeTags("node-v", ["awesome"], dry_run=True))
    self.assertHandler(rlib2.R_2_nodes_name_tags)
    self.assertItems(["node-v"])
    self.assertDryRun()
    self.assertQuery("tag", ["awesome"])

  def testDeleteNodeTags(self):
    self.rapi.AddResponse("16861")
    self.assertEqual(16861, self.client.DeleteNodeTags("node-w", ["awesome"],
                                                       dry_run=True))
    self.assertHandler(rlib2.R_2_nodes_name_tags)
    self.assertItems(["node-w"])
    self.assertDryRun()
    self.assertQuery("tag", ["awesome"])


if __name__ == '__main__':
  testutils.GanetiTestProgram()
