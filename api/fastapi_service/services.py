from database import engine, Base, SessionLocal, database
from models import City, CityProperty, Point
from database import CityAsync, CityPropertyAsync, PointAsync
from schemas import CityBase, PropertyBase, PointBase, RegionBase
from shapely.geometry.multilinestring import MultiLineString
from shapely.geometry.linestring import LineString
from geopandas.geodataframe import GeoDataFrame
from pandas.core.frame import DataFrame
from osm_handler import parse_osm
from typing import List, TYPE_CHECKING
import pandas as pd
import osmnx as ox
import os.path

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

def add_tables():
    return Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:        
        db.close()

def point_to_scheme(point : Point) -> PointBase:
    if point is None:
        return None

    return PointBase(latitude=point.latitude, longitude=point.longitude)

async def property_to_scheme(property : CityProperty) -> PropertyBase:
    if property is None:
        return None

    property_base = PropertyBase(population=property.population, population_density=property.population_density, time_zone=property.time_zone, time_created=str(property.time_created))
    # point = session.query(Point).filter(Point.id==property.id_center).first()
    query = PointAsync.select().where(PointAsync.c.id == property.id_center)
    point = await database.fetch_one(query)
    point_base = point_to_scheme(point=point)
    property_base.center = point_base
    
    return property_base

async def city_to_scheme(city : City) -> CityBase:
    if city is None:
        return None

    city_base = CityBase(id=city.id, city_name=city.city_name, downloaded=city.downloaded)
    #property = session.query(CityProperty).filter(CityProperty.id==city.id_property).first()
    query = CityPropertyAsync.select().where(CityPropertyAsync.c.id == city.id_property)
    property = await database.fetch_one(query)
    property_base = await property_to_scheme(property=property)
    city_base.property = property_base
    
    return city_base

async def cities_to_scheme_list(cities : List[City]) -> List[CityBase]:
    schemas = []
    for city in cities:
        schemas.append(await city_to_scheme(city=city))
    return schemas

async def get_cities(page: int, per_page: int) -> List[CityBase]:
    # with SessionLocal.begin() as session:
    #     cities = session.query(City).all()
    query = CityAsync.select()
    cities = await database.fetch_all(query)
    cities = cities[page * per_page : (page + 1) * per_page]
    return await cities_to_scheme_list(cities)

async def get_city(city_id: int) -> CityBase:
    # with SessionLocal.begin() as session:
    #     city = session.query(City).get(city_id)
    query = CityAsync.select().where(CityAsync.c.id == city_id)
    city = await database.fetch_one(query)
    return await city_to_scheme(city=city)

def add_info_to_db(city_df : DataFrame):
    with SessionLocal.begin() as session:
        city_name = city_df['Город']
        city_db = session.query(City).filter(City.city_name==city_name).first()
        downloaded = False
        if city_db is None:
            point_id = add_point_to_db(df=city_df)
            property_id = add_property_to_db(df=city_df, point_id=point_id)
            city_id = add_city_to_db(df=city_df, property_id=property_id)
        else:
            downloaded = city_db.downloaded

        file_path = f'./data/cities_osm/{city_name}.osm'
        if (not downloaded) and (os.path.exists(file_path)):
            add_graph_to_db(city_id=city_id, file_path=file_path)

def add_graph_to_db(city_id : int, file_path : str):
    with SessionLocal.begin() as session:
        city = session.query(City).get(city_id)
        ways, nodes = parse_osm(file_path)
        # add ways and nodes to DB
        print(f'DOWNLOADED: {city.city_name}')
        city.downloaded = True

def add_point_to_db(df : DataFrame) -> int:
    with SessionLocal.begin() as session:
        point = Point(latitude=df['Широта'], longitude=df['Долгота'])
        session.add(point)
        session.flush()
        return point.id

def add_property_to_db(df : DataFrame, point_id : int) -> int:
    with SessionLocal.begin() as session:
        # df['Федеральный округ']
        property = CityProperty(id_center=point_id, population=df['Население'], time_zone=df['Часовой пояс'])
        session.add(property)
        session.flush()
        return property.id

def add_city_to_db(df : DataFrame, property_id : int) -> int:
    with SessionLocal.begin() as session:
        city = City(city_name=df['Город'], id_property=property_id)
        session.add(city)
        session.flush()
        return city.id

def init_db():
    cities = pd.read_csv('./data/cities.csv')
    for row in range(0, cities.shape[0]):
        add_info_to_db(cities.loc[row, :])

async def download_info(city : City, extension : float) -> bool:
    filePath = f'./data/graphs/{city}.osm'
    if os.path.isfile(filePath):
        print(f'Exists: {filePath}')
        return True
    else:
        print(f'Loading: {filePath}')
        query = {'city': city.city_name}
        try:
            city_info = ox.geocode_to_gdf(query)

            north = city_info.iloc[0]['bbox_north']  
            south = city_info.iloc[0]['bbox_south']
            delta = (north-south) * extension / 200
            north += delta
            south -= delta

            east = city_info.iloc[0]['bbox_east'] 
            west = city_info.iloc[0]['bbox_west']
            delta = (east-west) * extension / 200
            east += delta
            west -= delta

            G = ox.graph_from_bbox(north=north, south=south, east=east, west=west, simplify=True, network_type='drive',)
            ox.save_graph_xml(G, filepath=filePath)
            return True

        except ValueError:
            print('Invalid city name')
            return False

def delete_info(city : City) -> bool:
    filePath = f'./data/graphs/{city}.osm'
    if os.path.isfile(filePath):
        os.remove(filePath)
        print(f'Deleted: {filePath}')
        return True
    else:
        print(f"File doesn't exist: {filePath}")
        return False
        

async def download_city(city_id : int, extension : float) -> CityBase:
    # with SessionLocal.begin() as session:
    #     city = session.query(City).get(city_id)
    query = CityAsync.select().where(CityAsync.c.id == city_id)
    city = await database.fetch_one(query)
    if city is None:
        return None
        
    city.downloaded = await download_info(city=city, extension=extension)

    return city_to_scheme(city=city)

async def delete_city(city_id : int) -> CityBase:
    # with SessionLocal.begin() as session:
    #     city = session.query(City).get(city_id)
    query = CityAsync.select().where(CityAsync.c.id == city_id)
    city = await database.fetch_one(query)
    if city is None:
        return None
        
    delete_info(city=city)
    city.downloaded = False
    return await city_to_scheme(city=city)

def to_list(polygon : LineString):
    list = []
    for x, y in polygon.coords:
        list.append([x, y])
    return list

def to_json_array(polygon):
    coordinates_list = []
    if type(polygon) == LineString:
       coordinates_list.append(to_list(polygon))
    elif type(polygon) == MultiLineString:
        for line in polygon.geoms:
            coordinates_list.append(to_list(line))
    else:
        raise ValueError("polygon must be type of LineString or MultiLineString")

    return coordinates_list

def region_to_schemas(regions : GeoDataFrame, ids_list : List[int], depth : int) -> List[RegionBase]:
    regions_list = [] 
    polygons = regions[regions['osm_id'].isin(ids_list)]
    for _, row in polygons.iterrows():
        id = row['osm_id']
        name = row['local_name']
        regions_array = to_json_array(row['geometry'].boundary)
        base = RegionBase(id=id, name=name, depth=depth, regions=regions_array)
        regions_list.append(base)

    return regions_list

def children(regions : GeoDataFrame, ids_list : List[int]):
    children = regions[regions['parents'].str.contains('|'.join(str(x) for x in ids_list), na=False)]
    return children['osm_id'].to_list()

def find_region_by_depth(city : City, regions : GeoDataFrame, depth : int) -> List[RegionBase]:
    if depth > 2 or depth < 0:
        return None

    ids_list = regions[regions['local_name']==city.city_name]['osm_id'].to_list()
    current_depth = 0

    while len(ids_list) != 0:
        if current_depth == depth:
            return region_to_schemas(regions=regions, ids_list=ids_list, depth=depth)

        ids_list = children(regions=regions, ids_list=ids_list)
        current_depth += 1

    return None


def get_regions(city_id : int, regions : GeoDataFrame, depth : int) -> List[RegionBase]:
    with SessionLocal.begin() as session:
        city = session.query(City).get(city_id)
    # query = CityAsync.select().filter(CityAsync.c.id == city_id)
    # city = database.fetch_one(query)
        if city is None:
            return None
        return find_region_by_depth(city=city, regions=regions, depth=depth)

async def graph_from_poly(id,polygon):
    pass

async def graph_from_id(city_id, region_id):
    pass