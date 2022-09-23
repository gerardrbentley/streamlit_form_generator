import ast
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List, Tuple
from zipfile import ZipFile

import httpx
import streamlit as st
from datamodel_code_generator import InputFileType, generate
from pydantic import BaseModel, HttpUrl
from streamlit.runtime.uploaded_file_manager import UploadedFile

use_example = "Example OpenAPI Specification"
use_upload = "Upload an OpenAPI Specification"
use_text_input = "Enter OpenAPI Specification in Text Input"
use_url = "Fetch OpenAPI Specification from a URL"

st.header("OAS -> Pydantic -> Streamlit Form Code Generator")
input_method = st.radio(
    label="How will you select your API Spec",
    options=[use_example, use_upload, use_text_input, use_url],
)

st.subheader(input_method)


@st.experimental_memo
def decode_uploaded_file(oas_file: UploadedFile) -> str:
    return oas_file.read().decode()


@st.experimental_memo
def decode_text_from_url(oas_url: str) -> str:
    try:
        response = httpx.get(oas_url, follow_redirects=True, timeout=10)
        return response.text
    except Exception as e:
        print(repr(e))
        return ""


class ValidURL(BaseModel):
    url: HttpUrl


def get_raw_oas(input_method: str) -> str:
    if input_method == use_example:
        st.write("This will demo how the app works!")
        oas_file = Path("quote-oas.json")
        raw_oas = oas_file.read_text()
    elif input_method == use_upload:
        st.write("This will let you use your own JSON or YAML OAS!")
        oas_file = st.file_uploader(
            label="Upload an OAS",
            type=["json", "yaml", "yml"],
            accept_multiple_files=False,
        )
        if oas_file is None:
            st.warning("Upload a file to continue!")
            st.stop()
        raw_oas = decode_uploaded_file(oas_file)
    elif input_method == use_text_input:
        st.write("This will parse raw text input into JSON or YAML OAS!")
        raw_oas = st.text_area(label="Enter OAS JSON or YAML text")
        if not len(raw_oas):
            st.warning("Enter OAS text to continue!")
            st.stop()
    elif input_method == use_url:
        st.write("This will fetch text from the URL containing a JSON or YAML OAS!")
        raw_oas_url = st.text_input(
            label="Enter the URL that hosts the OAS.",
        )
        try:
            oas_url = ValidURL(url=raw_oas_url)
        except Exception as e:
            print(repr(e))
            st.warning("Enter a valid HTTP(S) URL to continue!")
            st.stop()
        raw_oas = decode_text_from_url(oas_url.url)
    else:
        raise Exception("Unknown input_method")
    return raw_oas


raw_oas = get_raw_oas(input_method)
with st.expander("Show input OAS"):
    st.code(raw_oas)


@dataclass
class ModuleWithClasses:
    name: str
    code: str
    classes: List[str]


@st.experimental_memo()
def parse_into_modules(raw_oas: str) -> List[ModuleWithClasses]:
    with TemporaryDirectory() as temporary_directory_name:
        temporary_directory = Path(temporary_directory_name)
        module_files = generate_module_or_modules(raw_oas, temporary_directory)

        modules = []
        for module in module_files:
            module_code = module.read_text()

            module_ast = ast.parse(module_code)
            module_class_names = [
                x.name for x in module_ast.body if isinstance(x, ast.ClassDef)
            ]
            modules.append(
                ModuleWithClasses(
                    name=module.stem,
                    code=module_code,
                    classes=module_class_names,
                )
            )
    return modules


def generate_module_or_modules(raw_oas: str, output_directory: Path) -> List[Path]:
    output = Path(output_directory / "models.py")
    try:
        generate(
            raw_oas,
            input_file_type=InputFileType.OpenAPI,
            output=output,
            field_constraints=False,
        )
        return [output]
    except Exception as e:
        print(repr(e))
        try:
            generate(
                raw_oas,
                input_file_type=InputFileType.OpenAPI,
                output=output_directory,
                field_constraints=False,
            )
            return list(output_directory.iterdir())
        except Exception as e:
            print(repr(e))
            return []


modules = parse_into_modules(raw_oas)
if not len(modules):
    st.error("Couldn't find any models in the input!")
    st.stop()

st.success(f"Generated {len(modules)} module files")

all_module_models = []
for module in modules:
    import_name = module.name
    if import_name != "models":
        import_name = f"models.{import_name}"
    with st.expander(f"Show Generated Module Code: {import_name}"):
        st.code(module.code)

    for model_name in module.classes:
        all_module_models.append((module.name, model_name))


if len(all_module_models) > 1:
    selections = st.multiselect(
        label="Select Models that will be Form Inputs",
        options=all_module_models,
        default=all_module_models[0],
        format_func=lambda x: f"{x[0]}.{x[1]}",
    )
else:
    selections = list(all_module_models)


def generate_header(models_with_modules: List[Tuple[str, str]]) -> str:
    model_imports = []
    for module, class_name in models_with_modules:
        import_name = module
        if import_name != "models":
            import_name = f"models.{import_name}"
        model_imports.append(f"from {import_name} import {class_name}")
    import_code = "\n".join(model_imports)

    return f"""\
import streamlit as st
import streamlit_pydantic as sp

{import_code}

"""


def generate_single_model_form(model: str) -> str:
    return f"""\
input_data = sp.pydantic_form(key="pydantic_form", model={model})

{TRAILER}
"""


def generate_multi_model_form(models: List[str]) -> str:
    models_code = f"[{', '.join(models)}]"
    return f"""\
models = {models_code}
model = st.sidebar.radio(
    label="Which Model to Use in Form",
    options=models,
    format_func=lambda x: x.__name__,
)
input_data = sp.pydantic_form(key="pydantic_form", model=model)

{TRAILER}
"""


TRAILER = """\
if not input_data:
    st.warning("Submit the Form to continue")
    st.stop()

st.code(repr(input_data))
st.json(input_data.json())
"""


@st.experimental_memo
def generate_streamlit_code(selected_module_models: List[Tuple[str, str]]) -> str:
    streamlit_code = generate_header(selected_module_models)
    if len(selected_module_models) == 1:
        model_module, model = selected_module_models[0]
        streamlit_code += generate_single_model_form(model)
    else:
        models = [model for _, model in selected_module_models]
        streamlit_code += generate_multi_model_form(models)
    return streamlit_code


streamlit_code = generate_streamlit_code(selections)
with st.expander("Show Generated Streamlit App Code", True):
    st.code(body=streamlit_code, language="python")


@st.experimental_memo()
def zip_generated_code(modules: List[ModuleWithClasses], streamlit_code: str) -> bytes:
    with TemporaryDirectory() as temporary_directory_name:
        temporary_directory = Path(temporary_directory_name)
        if len(modules) == 1:
            model_directory = temporary_directory
        else:
            model_directory = temporary_directory / "models"
            model_directory.mkdir()

        zip_path = temporary_directory / "temp.zip"
        with ZipFile(zip_path, mode="w") as zip:
            for module in modules:
                module_path = model_directory / f"{module.name}.py"
                module_path.write_text(module.code)
                if len(modules) == 1:
                    destination = module_path.name
                else:
                    destination = f"models/{module_path.name}"
                zip.write(module_path, destination)
            streamlit_path = temporary_directory / "streamlit_app.py"
            streamlit_path.write_text(streamlit_code)
            zip.write(streamlit_path, streamlit_path.name)
        output = zip_path.read_bytes()
    return output


zip_bytes = zip_generated_code(modules, streamlit_code)
st.download_button(
    label="Download Zip of Generated Code",
    data=zip_bytes,
    file_name="generated_code.zip",
)
st.write("Download, Unzip, and run the code with `streamlit run streamlit_app.py`")
st.warning(
    """\
⚠️ Safety Note!

In general do NOT run random code from the internet on your machine.
The app generated from the example documentation is safe.
Apps generated from other sources may not be.
This app uses `ast.parse` to safely evaluate generated code; importing Python code will run the whole module!
"""
)
