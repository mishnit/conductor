import numpy as np
import pandas as pd
import math
import time
from collections import OrderedDict

from stations import station_names
from collect import get_TOD_reference, nice_time
from systemData import stationLoc, routeData
from aggregator import frequencyAggregator, NULL_STATS, segmentAggregator, durationAggregator

from pyproj import Proj
# EPSG Projection 2263 - NAD83 / New York Long Island (ftUS)
# http://spatialreference.org/ref/epsg/2263/
EPSG_PROJECTION_CODE = "2263"
FEET_PER_METER = 3.2808333333273816
_proj = Proj(init="epsg:"+EPSG_PROJECTION_CODE)
def _map_projection(lon, lat):
    x,y = _proj(lon, lat)
    return FEET_PER_METER*x, FEET_PER_METER*y

ONE_BY_SIXTY = 1./60
line_mult = 3
Z_UP = np.array((0., 0., 1.))

# color palette
BRICK = "#800000"
BLUE = "#0066CC"
GOLDENROD = "#FFCC00"
LT_YELLOW = "#FFE066"
LATE_MAGENTA = "#FF0066"
GREEN = "#009933"

def _color_for_status(c):
        if c < 0.05:
                return "rgba(0, 150, 100, 0.4)"
        else:
                c = min(1.0,c)
                lateRGB_init = (100, 150, 100, 0.25)
                lateRGB = (250, 0, 100, 1.0)
                lateDelta = 150.
                alphaDelta = lateRGB[3] - lateRGB_init[3]
                R = 250#int(lateRGB_init[0] + c*lateDelta)
                G = 0#int(lateRGB_init[1] - c*lateDelta)
                B = 100
                alpha =  lateRGB_init[3] + c*alphaDelta
                #print "color sequence",c,R,G,B
                return "rgba(" + str(R) + ", " + str(G) + ", " + str(B) + ", " + str(alpha) + ")"

def _stop_color_for_status(c):
        if c < 1.:
                return BLUE
        else:
                return GOLDENROD

def _color_for_y(y, ymin, ymax):
        r = (y-ymin)/(ymax-ymin)
        return "rgba(" + str(int(255*(1-r))) + ", " + str(0) + ", " + str(int(255*r)) + ", " + str(1) + ")"

def unit_perp(U, handedness=1.):
    U_mag = np.sqrt(np.dot(U,U))
    if U_mag == 0.:
       return np.array((0., 0.))
    U_xyz = np.array((U[0], U[1], 0.))
    V_xyz = np.cross(U_xyz, Z_UP)/U_mag
    return np.array((V_xyz[0], V_xyz[1]))

def _null_tag_for(trip_id):
    ll = trip_id.split("_")[1][0]
    dd = trip_id.split(".")[-1][0]
    null_string = ll + "_" + "_NULL_STOP_" + dd
    return null_string

# take list of tuples with arbitrary nested lists following the same format
# tuples are treated as a key followed by a list of values
# nested tuples and lists are indented
def _make_string(data, S="", indent=""):
    cr = "<br>"
    sp = "&nbsp&nbsp"
    for pair in data:
        addS = indent
        key = pair[0]
        vals = pair[1]
        if type(vals) == str:
            vals = [vals]
        if key:
            addS = addS + key + ": "
        # demand the same type of each list element here
        liveEntry = True
        if len(vals) == 0:
            liveEntry = False
        else:
            typeFlag = type(vals[0])
            if typeFlag == tuple:
                S = S + addS + cr
                addTup = indent
                for v in vals:
                    addTup = _make_string([v], addTup, sp)
                addS = addTup
            elif typeFlag == list:
                addList = indent
                for v in vals:
                    addList = _make_string([("",v)], addList, sp)
                addS = addS + addList
            elif typeFlag == str:
                addStrs = vals[0]
                for v in vals[1:]:
                    addStrs = addStrs + "; " + v
                addS = addS + addStrs
            addS = addS + cr
        if liveEntry:
            S = S + addS
    return S

# take a list of lists to create row and column entries
# complains if column numbers are inconsistent
def _make_table(data):
    #S = "<div id='table_in_hover'><table><tbody>"
    #S = "<div id='table_in_hover' style='z-index:10'><table style={display:block; position:absolute; z-index:100; left:0px; right;0px;}>"# backgroundColor:'#4C4E52'; color:'rgba(255,255,255,1)}>"
    S = "<div id='table_in_hover'><table style='color:white'>"# backgroundColor:'#4C4E52'; color:'rgba(255,255,255,1)}>"
    for row in data:
        S = S + "<tr>"
        for entry in row:
            S = S + "<td>" + entry + "&nbsp&nbsp</td>"
        S = S + "</tr>"#<br>"
    S = S + "</table></div>"
    #S = S + "</div>"
    return S

class systemManager():
	def __init__(self, setLines, setDirections):
                self.selectLines = setLines
                self.selectDirections = setDirections
                self.routeData = routeData()
                self.stationLoc = stationLoc()

                self.hover_fields = []#['name','time','location','schedule','data']
		self.plot_fields = ['x', 'y', 'color', 'size', 'alpha', 'formatted_string'] + self.hover_fields

                # for stops we need only use 'N' objects
                stopList = []
                stopIntervals = {}
                stopDurations = {}
                frequencyAggregators = {}
                self._durationAggregators = {}
                for ll in self.selectLines:
                        for dd in self.selectDirections:
                                frequencyAggregators[(ll,dd)] = frequencyAggregator(ll, dd)#, useComputed=False)
			        self._durationAggregators[(ll,dd)] = durationAggregator(ll,dd)#, useComputed=False)
                for ll in self.selectLines:
                    for ii in self.routeData.get(ll, "N")['id']:
                        stopList.append(ii)
                        ## catalog train arrival freqs
                        if not stopIntervals.get(ii):
                            stopIntervals[ii] = []
                        if not stopDurations.get(ii):
                            stopDurations[ii] = {}
                        for DD in self.selectDirections:
                                ii_DD = ii[:-1] + DD
                                stopIntervals[ii] = stopIntervals[ii] + [{(ll,DD):frequencyAggregators[(ll,DD)].fetchSeries(ii_DD)}]
                                stopDurations[ii][(ll,DD)] = self._durationAggregators[(ll,DD)].fetchSeries(ii_DD)
                    #for DD in self.selectDirections:
                    #    frequencyAggregators[(ll,DD)].storeData()
                stopList = set(stopList)
                self.stopSeries = pd.Series(index=stopList)
                for si in stopList:
                    self.stopSeries[si] = stopObj(si, self.stationLoc[si,:], self.plot_fields, stopIntervals[si], stopDurations[si])

		self.drawStationData = pd.DataFrame(columns=self.plot_fields)
		self.drawTrainData = pd.DataFrame(columns=self.plot_fields)
                self.drawRouteData = pd.DataFrame(columns=['x','y','alpha','size','color'])

                self._allRoutes = {}
                self._segmentAggregators = {}
                for ll in self.selectLines:
                    for dd in self.selectDirections:
                        routeSlice = self.routeData.get(ll, dd)
			self._segmentAggregators[(ll,dd)] = segmentAggregator(ll,dd)#, useComputed=False)
			#agg.select_range(--criteria--)
			#agg.reset()
                        for ri in routeSlice.index:
                                rData = routeSlice.loc[ri,:]
                                #print "instantiating route",ri,rData
				#routeStats = agg.process_tuple(rData['origin'], rData['destination'])
				routeStats = self._segmentAggregators[(ll,dd)].fetchSeries((rData['origin'], rData['destination']))
                                self._allRoutes[ri] = routeObj(ri, rData['origin'], rData['destination'], self.stationLoc, stats=routeStats)
                                self.drawRouteData.loc[ri,:] = self._allRoutes[ri].plotData()
                                targetStop = rData['destination']
                                if "NULL" in targetStop:
                                    continue 
                                elif "FINAL" in targetStop:
                                    continue 
                                else:
                                    targetStop = targetStop[:-1] + "N"
                                    self.stopSeries[targetStop].associateRoute(ll,dd,self._allRoutes[ri].trainsOnRoute)
                print "INITIALIZED ALL ROUTES",len(self._allRoutes)

                #self.activeTrains = {}
                self.activeTrains = OrderedDict()
		#print "Stop Data Loaded",self.stopSeries.index

        def plot_boundaries(self):
                lats = [stop['lat'] for stop in self.stopSeries]
                lons = [stop['lon'] for stop in self.stopSeries]
		sys_xmin, sys_xmax, sys_ymin, sys_ymax = self.stationLoc.get_area()
                ymin = min(lats)
                padding = 0.02*ymin
                ymin = ymin - padding
                ymax = max(lats) + padding
                xmin = min(lons) - padding
                xmax = max(lons) + 2*padding
                print "active boundaries",xmin,xmax,ymin,ymax
                sys_xmin, sys_ymin = _map_projection(sys_xmin, sys_ymin)
                sys_xmax, sys_ymax = _map_projection(sys_xmax, sys_ymax)
                print "system boundaries",sys_xmin, sys_xmax, sys_ymin, sys_ymax
		return xmin, xmax, ymin, ymax

        def selectData(self, newDF):
                newDF['line'] = newDF['trip_id'].map(lambda x: x.split("_")[1][0]) 
                newDF['direction'] = newDF['trip_id'].map(lambda x: x.split(".")[-1][0]) 
                newDF = newDF[newDF['line'].isin(self.selectLines)]
                #newDF = newDF[newDF['stop'].isin(self.stopSeries.index)]
                newDF = newDF[newDF['direction'].isin(self.selectDirections)]
                return newDF

        def streamUpdate(self, newDF):
                newDF = self.selectData(newDF)
                #print "culled stream data"
                #print newDF[['timestamp', 'trip_id','stop','arrive']]
                for i in newDF.index:
                        self._updateTrain(newDF.loc[i, 'trip_id'],
                                         newDF.loc[i, 'timestamp'],
                                         newDF.loc[i,'stop'],
                                         newDF.loc[i,'arrive'],
					 newDF.loc[i,'depart'])
                self._purgeStalled(newDF.loc[newDF.index[0],'timestamp'], 10.)

        def evolve(self, t_now, t_ref):
                hour = time.localtime(t_now)[3]
                for train in self.activeTrains.values():
                        routeOrigin, routeDest = train.attrib['routeID']
			train_id = train.attrib['id']
                        coords, isLate, progressFraction = self._getRoute(train_id, (routeOrigin, routeDest)).trainPosition(train_id, t_now)
                        segment_duration = self._getRoute(train_id, (routeOrigin, routeDest)).stats.get(hour).get("50%",0.)
                        ll = train_id.split("_")[1][0]
                        dd = train_id.split(".")[-1][0]
                        stopRecastID = routeDest[:-1] + "N"
                        trip_duration = self.stopSeries[stopRecastID].duration_dict.get((ll,dd),{}).get(hour,0)
                        train.update_position(t_now, coords, isLate, progressFraction, segment_duration, trip_duration, self.plot_fields)
                        self.drawTrainData.loc[train_id,:] = train.plotData()
                for stop_id in self.stopSeries.index:
                        self.stopSeries[stop_id].updateProgress(t_now, self.plot_fields)
                        self.drawStationData.loc[stop_id,:] = self.stopSeries[stop_id].plotData()

                # show segment patches where trains are running behind schedule
                for route in self._allRoutes.values():
                    alpha = 0.
                    for train in route.trainsOnRoute.keys():
                        t_late = self.activeTrains[train].attrib['t_late']
                        if t_late > 0:
                            alpha = min(0.5, alpha + 0.1*t_late)
                    #route.setPlotDataByDict([route.attrib['id']], {'x':[route.x_coords], 'y':[route.y_coords], 'alpha':[alpha]})

	def drawSystem(self, timestring):
		return self.drawStationData, self.drawTrainData, self.drawRouteData, self.plot_fields, self.hover_fields
	
        def _updateTrain(self, trip_id, timestamp, next_stop, t_arrive, t_depart):
		t_arrive = max(t_arrive, t_depart)
		if not self.activeTrains.get(trip_id):
                        prev_stop = self._lookupPrev(trip_id, next_stop)
        		self.activeTrains[trip_id] = trainObj(trip_id, prev_stop, next_stop, timestamp)
        		self._getRoute(trip_id, (prev_stop, next_stop)).addTrain(trip_id, timestamp, t_arrive)

		require_route_update, old_route_tuple, new_route_tuple = \
                        self.activeTrains[trip_id].update_trip(timestamp, next_stop, t_arrive)

                if require_route_update:
                        self._getRoute(trip_id, old_route_tuple).clearTrain(trip_id, timestamp)
                        self._getRoute(trip_id, new_route_tuple).addTrain(trip_id, timestamp, t_arrive)
                        oldStop = new_route_tuple[1][:-1] + "N"
                        self.stopSeries[oldStop].updateRecord(trip_id, timestamp)

        def _getRoute(self, train_id, (origin, dest)):
            ll = train_id.split("_")[1][0]
            dd = train_id.split(".")[-1][0]
            tag = origin + "_" + dest
            if not self._allRoutes.get(tag):
	        print "getRoute invoked route constructor between stations",origin,dest
                self._allRoutes[tag] = routeObj(tag, origin, dest, self.stationLoc, self._segmentAggregators[(ll,dd)].fetchSeries((origin, dest)))
            return self._allRoutes[tag]

        def _lookupPrev(self, trip_id, next_stop):
            result = self.routeData.data[self.routeData.data['destination']==next_stop]
            if len(result) == 0:
                #null_tag = _null_tag_for(trip_id)
                print "LOOKUP_PREV FAILED",next_stop,"RETURNING THE DESTINATION"
                return next_stop
            return result['origin'].values[0]

        def _purgeStalled(self, t_current, t_wait_mins):
            for train_id in self.activeTrains.keys():
                t_last_update = self.activeTrains[train_id].attrib['time_of_update']
                if (t_current - t_last_update)/60. > t_wait_mins:
                    routeID = self.activeTrains[train_id].attrib['routeID']
                    print nice_time(t_current), "Purging stalled train",train_id, "after", (t_current-t_last_update)/60.,"mins inactive on route",routeID
                    self._getRoute(train_id, routeID).clearTrain(train_id, t_current)
                    self.activeTrains.pop(train_id)
                    self.drawTrainData = self.drawTrainData.drop(train_id, axis=0)

class vizComponent():
	def __init__(self):
		self.storePlotData = []
		self.attrib = {}
	
	def data(self):
		return self.plotData.data()

	def __getitem__(self, item):
		if not self.attrib.get(item):
			print "ERROR: vizComponent.__getitem__()"
			print "Requested attribute",item,"not found.\nDefined attributes are",
			print self.attrib.keys()
		return self.attrib[item]

	def report(self):
		print "     attributes"
		for att in self.attrib.keys():
			print "    ",att, ":", self.attrib[att],
			if not self.attrib.get(att):
				print " <-- NOT initialized"
			else:
				print " "

        def setPlotData(self, fields, data):
            assert len(fields) == len(data)
	    self.storePlotData = data

        def plotData(self):
            return self.storePlotData

class trainObj(vizComponent):
	def __init__(self, train_id, prev_stop, next_stop, timestamp):
		vizComponent.__init__(self)
		self.attrib['id'] = train_id
		self.attrib['time_of_update'] = timestamp
                null_tag = _null_tag_for(train_id)
                self.attrib['next_stop'] = next_stop 
                self.attrib['prev_stop'] = prev_stop 
                self.attrib['routeID'] = (self.attrib['prev_stop'], self.attrib['next_stop'])
		self.attrib['sched_arrival'] = 0.
		self.attrib['trip_origin'] = self._calc_trip_origin(timestamp)
		self.attrib['last_stop_time'] = timestamp
                self.attrib['isLate'] = False
                self.attrib['name'] = self._make_train_name()
		self.attrib['t_late'] = 0.
		self.attrib['duration_actual'] = 0.
		self.attrib['segment_actual'] = 0.
		self.attrib['T_trip_composite'] = 0.
		self.attrib['t_segment_start'] = timestamp
		self.attrib['t_segment_stored'] = 0.
                self.attrib['status'] = "normal"
		self.update_count = 0

	def update_trip(self, time_of_update, next_stop, t_arrive):
                if self.attrib['trip_origin'] > self.attrib['time_of_update']:
                    self.attrib['status'] = "inactive"
		self.attrib['time_of_update'] = time_of_update
		self.attrib['sched_arrival'] = t_arrive
		self.attrib['duration_actual'] = (time_of_update - self.attrib['trip_origin'])/60.

                newStop = False
                old_route_tuple = self.attrib['routeID']

                ## passed a stop; update params for next stop
                if next_stop != self.attrib['next_stop']:
                        newStop = True
                        self.attrib['last_stop_time'] = time_of_update
                        self.attrib['prev_stop'] = self.attrib['next_stop']
		        self.attrib['next_stop'] = next_stop
                        self.attrib['routeID'] = (self.attrib['prev_stop'], self.attrib['next_stop'])
                        self.attrib['isLate'] = False
                        self.attrib['t_segment_start'] = time_of_update
                        self.attrib['T_trip_composite'] = self.attrib['T_trip_composite'] + self.attrib['t_segment_stored']
                        self.attrib['status'] = "normal"
                        self.attrib['t_late'] = 0.

                if time_of_update > t_arrive:
                        self.attrib['isLate'] = True
                        self.attrib['t_late'] = (time_of_update - t_arrive)/60.

                return newStop, old_route_tuple, self.attrib['routeID']

        def update_position(self, timestamp, coords, isLate, progressFraction, T_segment_avg, T_trip_avg, fields):
                self.attrib['segment_actual'] = (timestamp - self.attrib['t_segment_start'])/60.
                self.attrib['t_segment_stored'] = T_segment_avg/60.
                if T_trip_avg == 0:
                    lateFactor = 0.
                else:
                    t_trip_late = self.attrib['duration_actual'] - T_trip_avg
                    lateFactor = max(0.,t_trip_late)/self.attrib['duration_actual']
                self.update_count += 1
                self.attrib['isLate'] = isLate 
		self.attrib['t_late'] = max(0.,(timestamp - self.attrib['sched_arrival'])/60.)
                markerColor = _color_for_status(lateFactor)
                markerAlpha = 1.0
                if self.attrib['status'] == "inactive":
                        markerAlpha = 0.0
                self.setPlotData(fields,\
                                [coords[0],\
                                 coords[1],\
                                 markerColor,\
                                 float(12),\
                                 markerAlpha,\
                                _make_string([(self['name'],["(unique id " + self['id'] + ")"]),\
                                              ("Time", [nice_time(timestamp, military=False)])]) +\
                                    _make_table([\
                                                 ["Approaching next stop:&nbsp&nbsp", station_names[self['next_stop']]],\
                                                 ["Scheduled arrival&nbsp&nbsp", nice_time(self.attrib['sched_arrival'], military=False)],\
                                                 ["Behind schedule by&nbsp&nbsp", "%.1f" % float(self.attrib['t_late']) + " mins"]]) +\
                                    _make_table([\
                                                 ["","actual","past performance"],\
                                                 ["Time elapsed for this trip", "%.1f" % float(self.attrib['duration_actual']) + " mins", "%.1f" % float(T_trip_avg) + " mins"],\
                                                 ["Time elapsed on this segment", "%.1f" % float(self.attrib['segment_actual']) + " mins", "%.1f" % float(progressFraction*T_segment_avg/60.) + " mins"]])
                                             ])

        def _calc_trip_origin(self, current_time):
		t_ref = get_TOD_reference(current_time)
		minutes100 = float(self.attrib['id'].split("_")[0])
                t_origin = int(0.6*minutes100 + t_ref)
                if current_time - t_origin < -3600: #some trips are reported before departure so negative times will manifest
                        return t_origin - 86400 #subtract number of seconds in a day to handle midnight crossings
                else:
                        return t_origin

        def _make_train_name(self):
                ll = self['id'].split("_")[1][0]
                dd = self['id'].split(".")[-1][0]
                name_string = ll + " Train " + {'N':"Uptown", 'S':"Downtown"}[dd]
                return name_string

class stopObj(vizComponent):
	def __init__(self, stop_id, stopData, fields, interval_list, duration_dict):
		vizComponent.__init__(self)
		self.attrib['id'] = stop_id
                proj_x, proj_y = _map_projection(float(stopData['lon']), float(stopData['lat']))
		self.attrib['lon'] = proj_x
		self.attrib['lat'] = proj_y
		self.attrib['name'] = np.array(stopData['name'])
                self.routes = OrderedDict()
                self.lastStop = {}
                self._numStored = 3
                self.currentFreq = {}
                self.interval_list = interval_list
                self.duration_dict= duration_dict

        def associateRoute(self, ll, dd, trainsOnRouteDict):
            self.routes[(ll,dd)] = trainsOnRouteDict
            self.lastStop[(ll,dd)] = [time.time()]

        def updateRecord(self, trip_id, timestamp):
            ll = trip_id.split("_")[1][0]
            dd = trip_id.split(".")[-1][0]
            if not self.lastStop.get((ll,dd)):
                print self['id'],trip_id,"encountered unassociated route",ll,dd
                self.lastStop[(ll,dd)] = [timestamp]
            self.lastStop[(ll,dd)].append(timestamp)
            if len(self.lastStop[(ll,dd)]) > self._numStored:
                self.lastStop[(ll,dd)].pop(0)
            splits = [t_2 - t_1 for t_2,t_1 in zip(self.lastStop[(ll,dd)][1:], self.lastStop[(ll,dd)][:-1])]
            self.currentFreq[(ll,dd)] = sum(splits)/(60.*len(splits))

        def updateProgress(self, timestamp, fields):
                t_late_approaching, trains_approaching_string = self._listApproaching(timestamp)
                self.setPlotData(fields=fields,\
                                 data=[float(self.attrib['lon']),\
                                       float(self.attrib['lat']),\
                                       _stop_color_for_status(t_late_approaching/60.),\
                                       float(7),\
                                       float(1.0),\
                                       _make_string([("Station", [str(self['name'])]),\
                                                     ("Time", [nice_time(time.time(), military=False)]),\
                                                     ("Trains approaching", trains_approaching_string),\
                                                     ("Arrival Stats", [""])]) +\
                                       _make_table(self._listStopData(timestamp))])

        def _listApproaching(self, t_now):
            strings = []
            t_late = 0.
            for (ll,dd) in self.routes.keys():
                trainsDict = self.routes[(ll,dd)]
                listTrains = "; ".join([nice_time(t[1], military=False) for t in trainsDict.values()])
                for t_data in trainsDict.values():
                    t_late = t_late + max(0., t_now-t_data[1])
                direction_tag = {"N":"Uptown","S":"Downtown"}
                if len(listTrains) > 0:
                    strings.append((str(ll) + " " + direction_tag[dd] + " due", listTrains))
            return t_late, strings

        def _formatString(self, x_float):
            try:
                return str("%.0f" % float(x_float)) + " mins"
            except (ValueError, TypeError):
                return "not available"

        def _padString(self, string, L, left=True):
            #print "padding btwn",string,len(string),L
            padding = ""
            for i in range(L - len(string)):
                padding = padding + "&nbsp"
            #print "RETURNING ||",string + padding,"||"
            if not left:
                return  padding + string
            else:
                return string + padding

        def _listStopData(self, timestamp):
            strings = [["&nbsp&nbsp","Train Line","time since last","current frequency","past performance"]]
            hour = time.localtime(time.time())[3]
            #hour = time.localtime(timestamp)[3]
            for intervalSet in self.interval_list:
                ll,dd = intervalSet.keys()[0]
                freq = intervalSet[(ll,dd)].get(hour)
                if self.lastStop.get((ll,dd)):
                    if len(self.lastStop[(ll,dd)]) > 0:
                        t_waiting = float((timestamp - self.lastStop[(ll,dd)][-1])/60.)
                    direction_tag = {"N":"Uptown","S":"Downtown"}
                    strings.append(["&nbsp&nbsp", str(ll) + " " + direction_tag[dd], self._formatString(t_waiting), self._formatString(self.currentFreq.get((ll,dd))), self._formatString(freq)])
            return strings

class routeObj(vizComponent):
	def __init__(self, route_id, origin_id, destination_id, stationLoc, travel_time=1.0, stats=NULL_STATS):
		vizComponent.__init__(self)
		self.attrib['id'] = route_id
                self.attrib['origin_stop'] = origin_id
                self.attrib['dest_stop'] = destination_id
                self.attrib['travel_time'] = travel_time 
		self.stats = stats
                
                origin_lon, origin_lat = stationLoc[self.attrib['origin_stop'],'lon'], stationLoc[self.attrib['origin_stop'], 'lat']
                dest_lon, dest_lat = stationLoc[self.attrib['dest_stop'],'lon'], stationLoc[self.attrib['dest_stop'], 'lat']
                origin_x, origin_y = _map_projection(origin_lon, origin_lat)
                dest_x, dest_y = _map_projection(dest_lon, dest_lat)
                self.origin_coord = np.array((origin_x, origin_y))
                self.dest_coord = np.array((dest_x, dest_y))
                #print "ROUTE",self['id'], self.origin_coord, self.dest_coord
                self.x_coords = np.array((origin_x, dest_x))
                self.y_coords = np.array((origin_y, dest_y))
                #print "ROUTE",self['id'], self.x_coords, self.y_coords
                self.trainsOnRoute = OrderedDict()
                infoString = "route"
                ## todo this should be a dynamic update based on system time
                hour = time.localtime(time.time())[3]
                self.setPlotData(fields=['x','y','alpha','size','color'], data=[self.x_coords, self.y_coords, 1., 1., "#FFCC00"])

        def trainPosition(self, trip_id, timestamp, dir_shift=True):
                t_start, t_arrive, isLate = self.trainsOnRoute[trip_id]
                progress_fraction = max(0., float(timestamp - t_start)/(t_arrive - t_start))
                if progress_fraction > 0.95:
                        progress_fraction = 0.98
                        self.trainsOnRoute[trip_id] = t_start, t_arrive, True
                        isLate = True

		U = self.dest_coord - self.origin_coord
		if np.dot(U,U) == 0.:
                    return self.origin_coord, isLate, progress_fraction
		V = np.array((0., 0.))
		if dir_shift:
		    dd = trip_id.split(".")[-1][0]
		    sign = 1.
		    if dd=="S":
                        sign = -1.
		    V = unit_perp(U, sign)
		updateCoord = self.origin_coord + progress_fraction*U + 350.*V
                return updateCoord, isLate, progress_fraction

        def addTrain(self, trip_id, t_start, t_arrive):
		isLate = False
		if t_start > t_arrive:
                        hour = time.localtime(t_arrive)[3]
			t_arrive = t_start + self.stats.get(hour,0.).get('50%',0.)
			isLate = True
                self.trainsOnRoute[trip_id] = (t_start, t_arrive, isLate)

        def clearTrain(self, trip_id, timestamp):
                self.trainsOnRoute.pop(trip_id)
	
if __name__=="__main__":
        print "dataEngine::__main__"
