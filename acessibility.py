import os
import geopandas as gpd
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

HOST = os.getenv("PG_HOST", "localhost")
PORT = os.getenv("PG_PORT", "5432")
DATABASE = os.getenv("PG_DATABASE", "sgti_gis")
USER = os.getenv("PG_USER", "postgres")
PASSWORD = os.getenv("PG_PASSWORD", "")

engine = create_engine(f"postgresql://{USER}:{PASSWORD}@{HOST}:{PORT}/{DATABASE}")

def executar_estudo_acessibilidade_bts():
    with engine.begin() as conn:
        print("1. Criando camada unificada (ANTT + SEINFRA)...")
        conn.execute(text("""
            CREATE EXTENSION IF NOT EXISTS postgis;
            CREATE SCHEMA IF NOT EXISTS analise_acessibilidade;
            
            DROP TABLE IF EXISTS analise_acessibilidade.pontos_unificados CASCADE;
            
            CREATE TABLE analise_acessibilidade.pontos_unificados AS
            WITH seinfra_exclusivos AS (
                -- Paradas de gtfs.stops que NÃO foram pareadas na tabela da ANTT
                SELECT 
                    s.stop_id::text AS id_global,
                    'SEINFRA' AS sistema,
                    s.stop_name,
                    s.stop_lat::float AS stop_lat,
                    s.stop_lon::float AS stop_lon,
                    -- Gera o ponto espacial dinamicamente a partir de stop_lon e stop_lat:
                    ST_SetSRID(ST_MakePoint(s.stop_lon::float, s.stop_lat::float), 4326) AS geom
                FROM gtfs.stops s
                WHERE s.stop_id::text NOT IN (
                    SELECT DISTINCT stop_id_seinfra::text 
                    FROM gtfs_antt.stops 
                    WHERE stop_id_seinfra IS NOT NULL
                )
                  AND s.stop_lat IS NOT NULL 
                  AND s.stop_lon IS NOT NULL
            ),
            antt_e_integrados AS (
                -- Paradas da ANTT (e aquelas associadas à SEINFRA viram HUB)
                SELECT 
                    a.stop_id::text AS id_global,
                    CASE 
                        WHEN a.stop_id_seinfra IS NOT NULL THEN 'HUB_AMBOS_SISTEMAS' 
                        ELSE 'ANTT_INTERESTADUAL' 
                    END AS sistema,
                    a.stop_name,
                    a.stop_lat::float AS stop_lat,
                    a.stop_lon::float AS stop_lon,
                    ST_SetSRID(ST_MakePoint(a.stop_lon::float, a.stop_lat::float), 4326) AS geom
                FROM gtfs_antt.stops a
                WHERE a.stop_lat IS NOT NULL 
                  AND a.stop_lon IS NOT NULL
            )
            SELECT * FROM seinfra_exclusivos
            UNION ALL
            SELECT * FROM antt_e_integrados;

            -- Índice espacial GiST para consultas rápidas
            CREATE INDEX idx_pontos_unificados_geom 
            ON analise_acessibilidade.pontos_unificados USING GIST (geom);
        """))

        print("2. Calculando buffers de serviço (Padrão BTS - 40km / 25 milhas e Local - 5km)...")
        conn.execute(text("""
            DROP TABLE IF EXISTS analise_acessibilidade.buffers_servico;
            
            CREATE TABLE analise_acessibilidade.buffers_servico AS
            SELECT 
                'BTS_Regional_40km' AS nivel,
                40000 AS buffer_m,
                ST_UnaryUnion(ST_Collect(ST_Buffer(geom::geography, 40000)::geometry)) AS geom
            FROM analise_acessibilidade.pontos_unificados
            UNION ALL
            SELECT 
                'Acesso_Local_5km' AS nivel,
                5000 AS buffer_m,
                ST_UnaryUnion(ST_Collect(ST_Buffer(geom::geography, 5000)::geometry)) AS geom
            FROM analise_acessibilidade.pontos_unificados;

            CREATE INDEX idx_buffers_servico_geom 
            ON analise_acessibilidade.buffers_servico USING GIST (geom);
        """))

        # Consulta resumo dos pontos unificados
        df_resumo = pd.read_sql("""
            SELECT sistema, COUNT(*) AS total_pontos 
            FROM analise_acessibilidade.pontos_unificados 
            GROUP BY sistema
            ORDER BY total_pontos DESC;
        """, conn)
        
        print("\n=== Resumo dos Pontos Unificados ===")
        print(df_resumo.to_string(index=False))

    print("\nExecução finalizada com sucesso!")

if __name__ == "__main__":
    executar_estudo_acessibilidade_bts()
