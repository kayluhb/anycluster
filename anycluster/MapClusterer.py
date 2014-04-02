'''----------------------------------------------------------------------------

                                    DJANGO MAP CLUSTERING

                                    - kmeans
                                    - grid

VERSION: 0.1
AUTHOR: biodiv
LICENSE: GPL

----------------------------------------------------------------------------'''
from django.contrib.gis.geos import Point
from MapTools import MapTools

DEBUG = False

if DEBUG is False:
    from django.conf import settings
    from django.db.models import Q, Min
    from django.db.models.loading import get_model

    # get the model as defined in settings
    geoapp, geomodel = settings.ANYCLUSTER_GEODJANGO_MODEL.split('.')
    geo_column_str = settings.ANYCLUSTER_COORDINATES_COLUMN
    CLUSTER_FILTERS = getattr(settings, 'ANYCLUSTER_FILTERS', None)
    PINCOLUMN = getattr(settings, 'ANYCLUSTER_PINCOLUMN', None)

    if PINCOLUMN:
        pin_qry = [', MIN(%s) AS pinimg' % (PINCOLUMN), PINCOLUMN + ',']
    else:
        pin_qry = ['', '']

    Gis = get_model(geoapp, geomodel)

    geo_table = Gis._meta.db_table

    # get the table name

else:
    Gis = "SIGHTINGS_GIS"
    geo_column_str = 'coordinates'
    geo_table = 'SIGHTINGS_GIS'


'''----------------------------------------------------------------------------

    GRID CLUSTERING

    - receives viewport coordinates
    - gets map grid (coordinates -> mercator -> pixels)
    - counts markers in each cell
    - returns the cells as polygons (output srid = input srid) alongside the
      count

----------------------------------------------------------------------------'''
'''----------------------------------------------------------------------------

    KMEANS-GRID CLUSTERING

    - receives viewport coordinates
    - creates bigger cells than gridclusteronly
    - kmeans clusters in each cell
    - returns kmeans cluster points, not cells

----------------------------------------------------------------------------'''

'''-------------------------------------------------------------------------
VIEWPORT CORNERS TO TILES
having the pixel coordinates of the viewport corners (topright, bottomleft)
calculate on which cells the kmeans clustering has to be done.

 ----------                -----------
|          |              | 01  | 11  |
|          |              |     |     |
|   0/0    | --------->   |-----------|
|          |              | 00  | 10  |
|          |              |     |     |
 ----------                -----------
   zoom 0                     zoom 1
----------------------------------------------------------------------------'''


'''

    A VIEWPORT is defined by topright bottomleft coordinates + SRID

    4-------------a
    |             |
    |             |     a -> 2 -> b -> 4 -> a    ------> POLYGON: a.long a.lat ; a.long b.lat ; b.long b.lat; b.long a.lat ; a.long a.lat (long extends to right)
    |             |
    b-------------2


    format of viewport is a json array: [a_x, a_y, b_x, b_y]
        while x/y can be lat/lng or any other coordinate pair alongside the
        corresponding srid

    GEOS is lnglat, google latlng

'''


'''
zoom 0

== Tile Boundaries ==

WGS84 datum (longitude/latitude):
-180 -85.05112877980659
180 85.0511287798066

Spherical Mercator (meters):
-20037508.342789244 -20037508.342789244
20037508.342789244 20037508.342789244


Mercator max: 20037508
Mercator min: -20037508

Pixels at zoom 0:
0 0 256 256


World max: 40075016
World min: 40075016

'''

if DEBUG:
    viewport = {'left': -68.37890625, 'top': 75.8041001126815,
                'right': 68.37890625, 'bottom': -49.03257075030672}
    viewport2 = {'left': 161.89453125, 'top': 77.11983306610325,
                 'right': -61.34765625, 'bottom': -45.20731114149848}

    viewport3 = {'left': 136.078125, 'top': 76.75037266166416,
                 'right': 107.42578125, 'bottom': 2.5914519003430008}
    viewport4 = {'left': 22.90021780289203, 'top': 40.65626255144421, 'right':
                 23.001498015294374, 'bottom': 40.62369765879888}  # thessaloniki distance cluster


class MapClusterer():

    def __init__(self, zoom=1, gridSize=256, input_srid=4326, mapTileSize=256):
        self.input_srid = int(input_srid)
        self.gridSize = int(gridSize)
        self.zoom = int(zoom)
        self.maptools = MapTools(int(mapTileSize))
        self.valid_operators = ['=', '<', '>', '<=', '>=', 'list', '!list']

        if DEBUG == False:
            self.srid_db = self.getDatabaseSRID()

    def getDatabaseSRID(self):

        srid_qry = "SELECT id, ST_SRID(%s) FROM %s LIMIT 1;" % (
            geo_column_str, geo_table)
        srid_db_objs = Gis.objects.raw(srid_qry)

        if len(list(srid_db_objs)) > 0:
            srid_db = srid_db_objs[0].st_srid
        else:
            try:
                srid_db = settings.ANYCLUSTER_COORDINATES_COLUMN_SRID
            except:
                srid_db = 4326

        return srid_db

    def parseRequest(self, request):
        viewport = self.parseViewport(request)
        filters = self.parseFilters(request)
        return viewport, filters

    def parseViewport(self, request):

        viewport = {
            'left': request.GET['left'],
            'top': request.GET['top'],
            'right': request.GET['right'],
            'bottom': request.GET['bottom']
        }
        return viewport

    def parseFilters(self, request):

        filters = []

        if CLUSTER_FILTERS:
            for key in CLUSTER_FILTERS:
                if key != "id":
                    value_pre = request.GET.get(key, None)
                    if value_pre:
                        vwop = value_pre.split('_')

                        if len(vwop) == 2:
                            operator = vwop[0]
                            values_string = vwop[1]
                            values = values_string.split(',')

                            filters.append({
                                'column': key,
                                'values': values,
                                'operator': operator
                            })

                        else:
                            values = value_pre.split(',')

                            filters.append({'column': key, 'values': values})

        return filters

    # cache: {'filters':{}, 'cellIDs':[], 'zoom':1}, #returns clustercells
    def compareWithCache(self, request, filters, clustercells):

        clustercache = request.session.get('clustercache', {})

        deliver_cache = request.GET.get('cache', None)

        new_clustercells = []

        use_cache = False

        if clustercache and not deliver_cache:
            # clear cache if zoomlevel changed
            last_zoom = clustercache.get('zoom', None)

            if int(self.zoom) == int(last_zoom):

                applied_filters = clustercache.get('filters', [])

                if filters == applied_filters:
                    use_cache = True

        if use_cache:
            # changed for Django1.6 compatibility
            old_cells = set(clustercache['cellIDs'])
            new_clustercells = set(clustercells) - old_cells

            clustered_cells = old_cells.union(new_clustercells)

        else:
            clustered_cells = set(clustercells)
            new_clustercells = clustered_cells

        # changed for Django1.6 compatibility
        clustercache['cellIDs'] = list(clustered_cells)
        clustercache['filters'] = filters
        clustercache['zoom'] = self.zoom

        request.session['clustercache'] = clustercache

        return new_clustercells

    def constructFilterstring(self, filters):

        filterstring = ''

        for fltr in filters:

            # there can be multiple values
            values = fltr['values']
            column = fltr['column']
            operator = fltr.get('operator', None)

            if values:

                filterstring += ' AND ( '

                if column == 'time':

                    if operator is None or operator == 'seq':
                        days = values[0].split('-')
                        months = values[1].split('-')
                        years = values[2].split('-')

                    if operator is not None:

                        if operator in self.valid_operators:

                            filterstring += "%s %s TIMESTAMP '%s'" % (
                                column, operator, values[0])

                        elif operator == 'range':
                            filterstring += "%s >= TIMESTAMP '%s' AND %s <= TIMESTAMP '%s' " % (
                                column, values[0], column, values[1])

                        elif operator == 'seq':

                            filterstring += '''EXTRACT(YEAR FROM time) >= %s
                                            AND EXTRACT(YEAR FROM time) <= %s
                                            AND EXTRACT(MONTH FROM time) >= %s
                                            AND EXTRACT(MONTH FROM time) <= %s
                                            ''' % (years[0], years[1], months[0], months[1])

                    else:

                        filterstring += '''EXTRACT(YEAR FROM time) >= %s
                                            AND EXTRACT(YEAR FROM time) <= %s
                                            AND EXTRACT(MONTH FROM time) >= %s
                                            AND EXTRACT(MONTH FROM time) <= %s
                                            ''' % (years[0], years[1], months[0], months[1])

                else:

                    if operator == 'list' or operator == '!list':
                        if operator == 'list':
                            operator = 'IN'
                        else:
                            operator = 'NOT IN'

                        filterstring += ' %s %s (' % (column, operator)

                        first = True
                        for val in values:
                            if first:
                                filterstring += "'%s'" % val
                                first = False
                            else:
                                filterstring += ",'%s'" % val

                        filterstring += ')'

                    else:

                        valcounter = 0

                        for val in values:
                            if valcounter > 0:
                                filterstring += ' OR '

                            if operator and operator in self.valid_operators:
                                filterstring += "%s %s '%s' " % (column,
                                                                 operator, val)
                            else:
                                filterstring += "%s ~ '^%s.*' " % (column, val)

                            valcounter += 1

                filterstring += ')'

        return filterstring

    def distanceCluster(self, points, c_distance=50):

        # clusterdistance in pixels, as this is constant on every zoom level

        current_clist = []

        for point in points:
            point.id = [point.id]
            current_clist.append(point)

        count = len(current_clist)

        if count > 1:

            for c in range(count):

                cluster = current_clist.pop(c)

                # iterate over remaining, grab and remove all in range
                rcount = len(current_clist)

                remove_points = []

                for i in range(rcount):

                    point = current_clist[i]

                    clustercoords = getattr(cluster, geo_column_str)
                    pointcoords = getattr(point, geo_column_str)

                    dist = self.maptools.points_calcPixelDistance(
                        clustercoords, pointcoords, self.zoom)

                    if dist <= c_distance:

                        remove_points.append(i)
                        cluster.count += point.count
                        cluster.id += point.id

                        count += -1

                remove_points.reverse()
                for r in remove_points:
                    current_clist.pop(r)

                current_clist.insert(0, cluster)

                if c + 1 >= count:
                    break

        return current_clist

    # defaults to goole srid
    #viewport is {'top':1,'right':2,'bottom':3,'left':4}
    def gridCluster(self, clustercells, filters):

        if DEBUG:
            print('clustercells: %s' % clustercells)

        gridCells = []

        # turn each cell into a poly. for more speed iterate only once over the
        # cells
        for cell in clustercells:

            cell_topright, cell_bottomleft, poly = self.clusterCellToBounds(
                cell)

            if DEBUG:
                print('\n\npoly: %s' % poly)

            # get count within poly. poly transformed to database srid
            Q_filters = Q()
            lookup = "%s__within" % geo_column_str
            Q_filters.add(Q(**{lookup: poly}), Q.AND)

            # apply optional filters
            if filters:

                filterstring = self.constructFilterstring(filters)

                pin_count_pre = Gis.objects.raw(''' SELECT COUNT(*) AS id FROM %s WHERE ST_Within(%s, ST_GeomFromText('%s',%s) )
                                                %s
                                            ''' % (geo_table, geo_column_str, poly, self.srid_db, filterstring) )

                pin_count = int(pin_count_pre[0].id)

                if PINCOLUMN is not None and pin_count == 1:
                    pinimg_pre = Gis.objects.raw(''' SELECT %s AS id, %s AS %s FROM %s WHERE ST_Within(%s, ST_GeomFromText('%s',%s) )
                                                %s
                                            ''' % (PINCOLUMN, geo_column_str, geo_column_str, geo_table, geo_column_str, poly, self.srid_db, filterstring) )

                    pinimg = pinimg_pre[0].id
                    coordinates = getattr(pinimg_pre[0], geo_column_str)
                else:
                    pinimg = None

                # django orm fails on range of months across years, use raw if
                # filters are applied
                '''
                for fltr in filters:

                    E_filters = Q()
                    values = fltr['value'].split(',')
                    column = fltr['column']
                    operator = fltr.get('operator', None)

                    if column == 'time':

                        months = values[0].split('-')
                        years = values[1].split('-')

                        E_filters.add( (Q(time__year__gte = years[0]) & Q(time__year__lte = years[1]) & Q(time__month__gte = months[0]) & Q(time__month__lte = months[1])), Q.AND)

                    else:

                        for val in values:
                            if operator:
                                if operator == '=':
                                    lookup = "%s =" %column
                                elif operator == '<':
                                    lookup = "%s__lt" %column
                                elif operator == '<=':
                                    lookup = "%s__lte" %column
                                elif operator == '>':
                                    lookup = "%s__gt" %column
                                elif operator == '>=':
                                    lookup = "%s__gte" %column
                            else:
                                lookup = "%s__startswith" %column
                            E_filters.add(Q(**{lookup:val}), Q.OR)

                    Q_filters.add(E_filters, Q.AND)
            '''
            else:
                if PINCOLUMN:
                    pin_count = Gis.objects.filter(Q_filters).count()

                    if pin_count == 1:
                        pinimg_pre = Gis.objects.filter(Q_filters)[0]
                        pinimg = getattr(pinimg_pre, PINCOLUMN)
                        coordinates = getattr(pinimg_pre, geo_column_str)
                    else:
                        pinimg = None

                else:
                    pin_count = Gis.objects.filter(Q_filters).count()
                    pinimg = None

            # transform the polys to output srid if necessary
            if self.srid_db != self.input_srid:
                self.maptools.point_AnyToAny(
                    cell_topright, self.srid_db, self.input_srid)
                self.maptools.point_AnyToAny(
                    cell_bottomleft, self.srid_db, self.input_srid)

            # construct a square for grid nodes
            x = 'x'
            y = 'y'
            nodes = [
                {x: cell_topright.x, y: cell_topright.y},
                {x: cell_topright.x, y: cell_bottomleft.y},
                {x: cell_bottomleft.x, y: cell_bottomleft.y},
                {x: cell_bottomleft.x, y: cell_topright.y}
            ]

            if int(pin_count) == 1 and PINCOLUMN is not None:
                center_pre = Point(
                    coordinates.x, coordinates.y, srid=self.srid_db)

                if self.srid_db != self.input_srid:
                    self.maptools.point_AnyToAny(
                        center_pre, self.srid_db, self.input_srid)

                center_x = center_pre.x
                center_y = center_pre.y

            else:
                center_x = (cell_topright.x + cell_bottomleft.x) / 2
                center_y = (cell_topright.y + cell_bottomleft.y) / 2

            center = {x: center_x, y: center_y}

            cellobj = {'cell': nodes, 'count': pin_count,
                       'center': center, 'pinimg': pinimg}

            if DEBUG:
                print('\n\noutput: %s ' % nodes)

            gridCells.append(cellobj)

        return gridCells

    def getKmeansClusterContent(self, x, y, kmeansList, filters):
        # return all IDs of the pins contained by a cluster

        cluster = Point(x, y, srid=self.input_srid)

        cell = self.maptools.getCellIDForPoint(
            cluster, self.zoom, self.gridSize)

        cell_str = ",".join(str(c) for c in cell)

        cell_topright, cell_bottomleft, poly = self.clusterCellToBounds(
            cell_str)

        kmeans_string = (",").join(str(k) for k in kmeansList)

        if filters:

            filterstring = self.constructFilterstring(filters)

        else:

            filterstring = ""

        entries = Gis.objects.raw('''SELECT *
                        FROM (
                          SELECT kmeans(ARRAY[ST_X(%s), ST_Y(%s)], 6) OVER (), %s.*
                          FROM %s
                          WHERE ST_Within(%s, ST_GeomFromText('%s',%s) ) %s
                        ) AS ksub
                        WHERE kmeans IN (%s);
                    ''' % (geo_column_str, geo_column_str, geo_table, geo_table, geo_column_str, poly, self.srid_db, filterstring, kmeans_string)
        )

        return entries

    def getClusterParameters(self, request):

        viewport, filters = self.parseRequest(request)
        # get the clustering cells
        clustercells_pre = self.getClusterCells(viewport)
        # clean the cells
        clustercells = self.compareWithCache(
            request, filters, clustercells_pre)

        return clustercells, filters

    def kmeansCluster(self, clustercells, filters):

        pins = []

        # kmeans cluster in each cell
        for cell in clustercells:

            cell_topright, cell_bottomleft, poly = self.clusterCellToBounds(
                cell)

            if filters:

                filterstring = self.constructFilterstring(filters)

            else:

                filterstring = ""

            cellpins = Gis.objects.raw(
                '''SELECT kmeans AS id, count(*), ST_Centroid(ST_Collect(%s)) AS %s %s
                            FROM (
                              SELECT %s kmeans(ARRAY[ST_X(%s), ST_Y(%s)], 6) OVER (), %s
                              FROM %s
                              WHERE ST_Within(%s, ST_GeomFromText('%s',%s) ) %s
                            ) AS ksub
                            GROUP BY kmeans
                            ORDER BY kmeans;
                        ''' % (geo_column_str, geo_column_str, pin_qry[0], pin_qry[1], geo_column_str, geo_column_str, geo_column_str, geo_table, geo_column_str, poly, self.srid_db, filterstring)
            )

            # clean the clusters
            cellpins = self.distanceCluster(list(cellpins))

            if DEBUG:
                print('pins after phase2: %s' % cellpins)

            for cell in cellpins:
                point = getattr(cell, geo_column_str)

                # calculate the radius in METERS
                '''
                if 'POLYGON' in cell.nodes:
                    rimnode = cell.nodes.lstrip('POLYGON((').strip('INT(').rstrip('))').split(',')[0]

                    rimx, rimy = rimnode.split(' ')
                    rimpoint = Point(float(rimx),float(rimy), srid=srid_db)

                    if srid_db != 4326:
                        center = point.clone()
                        self.maptools.point_ToLatLng(rimpoint)
                        self.maptools.point_ToLatLng(center)

                    #calc distance
                    geod = pyproj.Geod(ellps='WGS84')

                    try:
                        angle1,angle2,distance = geod.inv(center.x, center.y, rimpoint.x, rimpoint.y)
                        #distance = math.sqrt( (float(rimx)-point.x)**2 + (float(rimy)-point.x)**2 )
                        #distance = center.distance(rimpoint)
                    except:
                        distance = 0

                elif 'POINT' in cell.nodes:
                    distance = 0

                else:
                    distance = 0

                if point.srid != self.input_srid:
                    self.maptools.point_AnyToAny(point, point.srid, self.input_srid)

                pins.append({'count':cell.count, 'x':point.x, 'y':point.y, 'radius':distance})
                '''

                if point.srid != self.input_srid:
                    self.maptools.point_AnyToAny(
                        point, point.srid, self.input_srid)

                if PINCOLUMN:
                    pinimg = cell.pinimg
                else:
                    pinimg = None

                pins.append({'ids': cell.id, 'count': cell.count, 'center': {
                            'x': point.x, 'y': point.y}, 'pinimg': pinimg})

        return pins

    # returns a poly for search and bounds for map display
    def clusterCellToBounds(self, cell):

        bounds = []

        pixelbounds = self.maptools.cellIDToTileBounds(cell, self.gridSize)
        mercatorbounds = self.maptools.bounds_PixelToMercator(
            pixelbounds, self.zoom)

        # convert mercatorbounds to latlngbounds
        cell_topright = Point(
            mercatorbounds['right'], mercatorbounds['top'], srid=3857)
        cell_bottomleft = Point(
            mercatorbounds['left'], mercatorbounds['bottom'], srid=3857)
        self.maptools.point_ToLatLng(cell_topright)
        self.maptools.point_ToLatLng(cell_bottomleft)

        # if it is not a latlng database, convert the polygons
        if self.srid_db != 4326:
            self.maptools.point_AnyToAny(cell_topright, 4326, self.srid_db)
            self.maptools.point_AnyToAny(cell_bottomleft, 4326, self.srid_db)

        poly = self.maptools.bounds_ToPolyString({'top': cell_topright.y, 'right': cell_topright.x,
                                                  'bottom': cell_bottomleft.y, 'left': cell_bottomleft.x})

        if DEBUG:
            print '%s' % poly

        return cell_topright, cell_bottomleft, poly

    '''-------------------------------------------------------------------------------------------------------------------

       LatLng --------> Meters (Mercator) ---------> Shifted origin ---------> pixel coords ---------> GRID, depending on tilesize

     -----------           -----------                -----------              -----------
    |           |         |           |              |           |            |           | 
    |           |         |           |              |           |            |           | 
    |     O     |         |     O     |              |           |            |           | 
    |           |         |           |              |           |            |           | 
    |           |         |           |              |           |            |           | 
     -----------           -----------               O-----------             O-----------
       LATLNG                 METERS                     METERS                   PIXELS
                                                 (shifted coordinates)
      
    O = origin

    The coordinate system with shifted origin has only coordinates with positive values.
    This makes it possible to calculate a quadKey value for each marker.
    --------------------------------------------------------------------------------------------------------------------'''

    # returns QuadKey IDS on the viewport according to a defined tilesize
    def getClusterCells(self, viewport):

        # if DEBUG:
        #    print('VIEWPORT(wgs84datum, 4326, longlat): %s' %viewport)

        # create points according to input srid
        topright = Point(
            float(viewport['right']), float(viewport['top']), srid=self.input_srid)
        bottomleft = Point(
            float(viewport['left']), float(viewport['bottom']), srid=self.input_srid)

        if self.input_srid != 4326:
            topright = self.maptools.point_ToLatLng(topright)
            bottomleft = self.maptools.point_ToLatLng(bottomleft)

        # Polar areas with abs(latitude) bigger then 85.05112878 are clipped
        # off.
        if topright.y > 85.0:
            topright.y = 85.0

        if topright.x > 179.9999:
            topright.x = 179.9999

        if bottomleft.y < -85:
            bottomleft.y = -85

        if bottomleft.x < -179.9999:
            bottomleft.x = -179.9999

        if DEBUG:
            print('4326, longlat: topright: (%s,%s) | bottomleft: (%s,%s)' %
                  (topright.x, topright.y, bottomleft.x, bottomleft.y))

        # project points to mercator 3875, plane coordinates
        self.maptools.point_ToMercator(topright)
        self.maptools.point_ToMercator(bottomleft)

        if DEBUG:
            print('MERCATOR: topright: (%s,%s) | bottomleft: (%s,%s)' %
                  (topright.x, topright.y, bottomleft.x, bottomleft.y))

        # shift origin
        self.maptools.point_MercatorToWorld(topright)
        self.maptools.point_MercatorToWorld(bottomleft)

        if DEBUG:
            print('WORLD: topright: (%s,%s) | bottomleft: (%s,%s)' %
                  (topright.x, topright.y, bottomleft.x, bottomleft.y))

        # calculate pixelcoords from world coords depending on zoom
        self.maptools.point_WorldToPixels(topright, self.zoom)
        self.maptools.point_WorldToPixels(bottomleft, self.zoom)

        if DEBUG:
            print('PIXELS: topright: (%s,%s) | bottomleft: (%s,%s)' %
                  (topright.x, topright.y, bottomleft.x, bottomleft.y))

        # get topright and bottom left cellID, e.g. (03,01)
        toprightCell = self.maptools.point_PixelToCellID(
            topright, self.gridSize)
        bottomleftCell = self.maptools.point_PixelToCellID(
            bottomleft, self.gridSize)

        if DEBUG:
            print('CELLID: toprightCell: %s  |  bottomleftCell: %s' %
                  (toprightCell, bottomleftCell))

        # get all Cells that need to be clustered
        clusterCells = self.maptools.get_ClusterCells(
            toprightCell, bottomleftCell)

        # from ID-list create list of polygons
        return clusterCells

    def getBounds(self, filterstring, output_srid=4326):

        if filterstring:
            filterstring = filterstring.lstrip(' AND ( ').replace(')', '', 1)
            filterstring = ' WHERE ' + filterstring

        # get the minimum bounding box. django cant handle Box() geometries, so
        # we use ST_xmaxmin isntead of ST_Extent
        envelope = Gis.objects.raw('''SELECT ST_xMin(ST_Collect(%s)) AS id, ST_xMax(ST_Collect(%s)) as xmax,
                                        ST_yMin(ST_Collect(%s)) as ymin, ST_yMax(ST_Collect(%s)) as ymax FROM %s %s;''' % (geo_column_str, geo_column_str,
                                                                                                                           geo_column_str, geo_column_str,
                                                                                                                           geo_table, filterstring))

        xmin = envelope[0].id
        xmax = envelope[0].xmax
        ymin = envelope[0].ymin
        ymax = envelope[0].ymax

        # convert bounds to wanted srid
        bottomleft = Point(xmin, ymin, srid=self.srid_db)
        topright = Point(xmax, ymax, srid=self.srid_db)
        self.maptools.point_AnyToAny(bottomleft, self.srid_db, output_srid)
        self.maptools.point_AnyToAny(topright, self.srid_db, output_srid)

        # google maps has fitbounds for bottomleft (sw) topright(ne) bounds
        # openlayers has getZoomForExtent, so zoom/center calculation is not needed, bounds are enough
        # furthermore, the clusterer should be able to display on all maps, so including tilesizes etc here
        # would limit compatibility

        return {'left': bottomleft.x, 'top': topright.y, 'right': topright.x, 'bottom': bottomleft.y}
