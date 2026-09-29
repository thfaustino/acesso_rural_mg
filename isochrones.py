import os
import time
import openrouteservice
import geopandas as gpd
import pandas as pd
from shapely.geometry import shape
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

HOST = os.getenv("PG_HOST", "localhost")
PORT = os.getenv("PG_PORT", "5432")
DATABASE = os.getenv("PG_DATABASE", "sgti_gis")
USER = os.getenv("PG_USER", "postgres")
PASSWORD = os.getenv("PG_PASSWORD", "")

engine = create_engine(f"postgresql://{USER}:{PASSWORD}@{HOST}:{PORT}/{DATABASE}")
API_KEY = os.getenv("KEY_OSM")
client = openrouteservice.Client(key=API_KEY)


def calcular_isocronas_mg(tempos_minutos=[30, 60]):
    print("1. Filtrando e clusterizando pontos exclusivos de Minas Gerais...")

    # Parâmetros posicionais: ST_ClusterDBSCAN(geom, 0.05, 1) sem usar :=
    query_polos_mg = text("""
        WITH pontos_mg AS (
            SELECT 
                id_global,
                stop_name,
                sistema,
                geom,
                ST_ClusterDBSCAN(geom, 0.05, 1) OVER() AS cluster_id
            FROM analise_acessibilidade.pontos_unificados
            WHERE (
                sistema IN ('SEINFRA', 'HUB_AMBOS_SISTEMAS') 
                OR stop_name ILIKE '%- MG%' 
                OR stop_name ILIKE '%MINAS GERAIS%'
            )
            AND geom IS NOT NULL
        )
        SELECT 
            cluster_id,
            COUNT(*) AS total_paradas_no_cluster,
            ST_X(ST_Centroid(ST_Collect(geom))) AS centro_lon,
            ST_Y(ST_Centroid(ST_Collect(geom))) AS centro_lat,
            (ARRAY_AGG(stop_name))[1] AS nome_referencia
        FROM pontos_mg
        GROUP BY cluster_id
        ORDER BY total_paradas_no_cluster DESC;
    """)

    # Executa usando text() e conexão direta
    with engine.connect() as conn:
        df_polos = pd.read_sql(query_polos_mg, conn)

    total_polos = len(df_polos)
    print(f"Total de polos/cidades em MG a processar: {total_polos}")

    ranges_seg = [m * 60 for m in tempos_minutos]
    isocronas = []

    print("\n2. Disparando cálculo de isócronas...")
    for idx, row in df_polos.iterrows():
        coords = [float(row['centro_lon']), float(row['centro_lat'])]

        try:
            iso = client.isochrones(
                locations=[coords],
                profile='driving-car',
                range=ranges_seg,
                smoothing=0.7
            )

            for feat in iso.get('features', []):
                geom = shape(feat['geometry'])
                minutos = int(feat['properties']['value'] / 60)
                isocronas.append({
                    'cluster_id': row['cluster_id'],
                    'polo_referencia': row['nome_referencia'],
                    'tempo_min': minutos,
                    'geometry': geom
                })

            time.sleep(1.6)

        except Exception as e:
            print(f"Erro no polo {row['nome_referencia']}: {e}")
            if "Quota exceeded" in str(e):
                print("Cota temporária atingida. Aguardando 60 segundos...")
                time.sleep(60)
            continue

        if (idx + 1) % 10 == 0 or (idx + 1) == total_polos:
            print(f"Progresso: {idx + 1}/{total_polos} polos concluídos...")

    if not isocronas:
        print("Nenhuma isócrona gerada.")
        return

    # 3. Consolidação e Dissolve
    print("\n3. Gerando mancha consolidada de Minas Gerais...")
    gdf = gpd.GeoDataFrame(isocronas, crs="EPSG:4326")
    gdf_dissolvido = gdf.dissolve(by='tempo_min', as_index=False)

    # 4. Salvar no PostGIS
    print("4. Gravando no PostgreSQL...")
    gdf_dissolvido.to_postgis(
        name="isocronas_mg_dissolvidas",
        con=engine,
        schema="analise_acessibilidade",
        if_exists="replace",
        index=False
    )
    print("Concluído! Camada gerada: 'analise_acessibilidade.isocronas_mg_dissolvidas'")


if __name__ == "__main__":
    calcular_isocronas_mg()