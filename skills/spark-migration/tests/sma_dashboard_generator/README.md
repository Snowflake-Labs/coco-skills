# Tests para sma-dashboard-generator

Este directorio contiene los tests del skill SMA Dashboard Generator.

## Estructura

```
tests/sma_dashboard_generator/
├── conftest.py              # Fixtures compartidos
├── fixtures/
│   └── sample_issues.csv    # CSV de prueba con 9 registros
├── test_ewi_extractor.py    # Unit tests del extractor (36 tests)
├── test_sma_manager.py      # Unit tests del manager (25 tests)
└── test_e2e.py              # Tests end-to-end (16 tests)
```

## Ejecutar tests

```bash
# Todos los tests del skill
python3 -m pytest tests/sma_dashboard_generator/ -v

# Solo unit tests
python3 -m pytest tests/sma_dashboard_generator/test_ewi_extractor.py tests/sma_dashboard_generator/test_sma_manager.py -v

# Solo E2E tests
python3 -m pytest tests/sma_dashboard_generator/test_e2e.py -v

# Con coverage
python3 -m pytest tests/sma_dashboard_generator/ --cov=skills/spark-migration/snowpark-api/sma-dashboard-generator/scripts -v

# Test específico
python3 -m pytest tests/sma_dashboard_generator/test_ewi_extractor.py::TestGetEwiUrl::test_sprkpy_code_returns_python_docs_url -v
```

## Cobertura por archivo

### test_ewi_extractor.py (Unit Tests)

Tests para `scripts/extractors/ewi_extractor.py`:

| Clase | Función testeada | Tests |
|-------|------------------|-------|
| `TestGetEwiUrl` | `get_ewi_url()` | 8 |
| `TestParseIssuesCsv` | `parse_issues_csv()` | 5 |
| `TestAggregateEwis` | `aggregate_ewis()` | 8 |
| `TestAggregateFiles` | `aggregate_files()` | 6 |
| `TestGenerateSummary` | `generate_summary()` | 3 |
| `TestExtractEwiData` | `extract_ewi_data()` | 6 |

### test_sma_manager.py (Unit Tests)

Tests para `scripts/sma_manager.py`:

| Clase | Función testeada | Tests |
|-------|------------------|-------|
| `TestNormalizePath` | `normalize_path()` | 4 |
| `TestGetFileIconClass` | `get_file_icon_class()` | 7 |
| `TestGenerateFilesListHtml` | `generate_files_list_html()` | 5 |
| `TestGenerateCategoryOptions` | `generate_category_options()` | 4 |
| `TestFindOutputBase` | `find_output_base()` | 5 |

### test_e2e.py (End-to-End Tests)

Tests del flujo completo:

| Clase | Qué testea | Tests |
|-------|------------|-------|
| `TestDashboardGeneration` | Generación de estructura de archivos, JSON, HTML | 6 |
| `TestServerAPI` | Endpoints HTTP: `/health`, `/api/ewi/update`, `/api/file/update` | 5 |
| `TestDataPersistence` | Preservación de status al regenerar dashboard | 2 |
| `TestStartServerScript` | Script `start_server.py` (--status, --list) | 3 |

## Fixtures disponibles

Definidos en `conftest.py`:

| Fixture | Descripción |
|---------|-------------|
| `fixtures_dir` | Path al directorio `fixtures/` |
| `sample_csv_path` | Path a `sample_issues.csv` |
| `temp_output_dir` | Directorio temporal para outputs |
| `sample_ewi_records` | Lista de registros EWI para tests de agregación |
| `sample_aggregated_ewis` | EWIs agregados para tests de summary |

Definidos en `test_e2e.py`:

| Fixture | Descripción |
|---------|-------------|
| `e2e_workspace` | Workspace completo simulando output de SMA |
| `generated_dashboard` | Dashboard generado listo para tests |
| `running_server` | Servidor HTTP corriendo en puerto aleatorio |

## Agregar nuevos tests

1. **Unit tests**: Agregar a `test_ewi_extractor.py` o `test_sma_manager.py`
2. **E2E tests**: Agregar a `test_e2e.py`
3. **Nuevos fixtures**: Agregar a `conftest.py` si son compartidos

Ejemplo de nuevo test:

```python
# En test_ewi_extractor.py
class TestNewFeature:
    def test_feature_does_something(self, sample_csv_path):
        result = new_function(sample_csv_path)
        assert result == expected_value
```

## Datos de prueba

`fixtures/sample_issues.csv` contiene 9 registros con:

- 3 códigos EWI únicos: `SPRKPY-1001`, `SPRKPY-2002`, `SSC-EWI-0001`, `SSC-FDM-0005`, `SPRKSCL-1234`, `SPRKSQL-5678`
- 4 categorías: Conversion, Performance, SQL, Functional
- 7 archivos afectados
