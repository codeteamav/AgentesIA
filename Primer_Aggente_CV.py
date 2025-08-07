from dotenv import load_dotenv
from openai import OpenAI
import json
import os
import requests
from pypdf import PdfReader
import gradio as gr
import mysql.connector
from mysql.connector import Error
from pydantic import BaseModel, constr
from typing import Optional, List

load_dotenv(override=True)

modelo_openai = os.getenv('modelo_openai')

def push(text):
    requests.post(
        "https://api.pushover.net/1/messages.json",
        data={
            "token": os.getenv("PUSHOVER_TOKEN"),
            "user": os.getenv("PUSHOVER_USER"),
            "message": text,
        }
    )


def record_user_details(email, name="Nombre no indicado", notes="no proporcionadas"):
    push(f"Registrando {name} con email {email} y notas {notes}")
    return {"recorded": "ok"}

def record_unknown_question(question):
    push(f"Registrando {question}")
    return {"recorded": "ok"}

record_user_details_json = {
    "name": "record_user_details",
    "description": "Utiliza esta herramienta para registrar que un usuario está interesado en estar en contacto y proporcionó una dirección de correo electrónico.",
    "parameters": {
        "type": "object",
        "properties": {
            "email": {
                "type": "string",
                "description": "La dirección de email del usuario"
            },
            "name": {
                "type": "string",
                "description": "El nombre del usuario, si se indica"
            }
            ,
            "notes": {
                "type": "string",
                "description": "¿Alguna información adicional sobre la conversación que valga la pena registrar para dar contexto?"
            }
        },
        "required": ["email"],
        "additionalProperties": False
    }
}

record_unknown_question_json = {
    "name": "record_unknown_question",
    "description": "Utiliza siempre esta herramienta para registrar cualquier pregunta que no haya podido responder porque no se sabía la respuesta.",
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "La pregunta no sabe responderse"
            },
        },
        "required": ["question"],
        "additionalProperties": False
    }
}

tools = [{"type": "function", "function": record_user_details_json},
        {"type": "function", "function": record_unknown_question_json}]

class Evaluation(BaseModel):
    is_acceptable: bool
    feedback: str

class PreguntaRespuesta(BaseModel):
    pregunta: constr(max_length=5000)
    respuesta: constr(max_length=5000)

class Me:

    def __init__(self):
        self.openai = OpenAI()
        self.name = "Andre Felipe Vega"
        reader = PdfReader("me/linkedin.pdf")
        self.linkedin = ""
        for page in reader.pages:
            text = page.extract_text()
            if text:
                self.linkedin += text
        with open("me/summary.txt", "r", encoding="utf-8") as f:
            self.summary = f.read()


    def handle_tool_call(self, tool_calls):
        results = []
        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            arguments = json.loads(tool_call.function.arguments)
            print(f"Tool called: {tool_name}", flush=True)
            tool = globals().get(tool_name)
            result = tool(**arguments) if tool else {}
            results.append({"role": "tool","content": json.dumps(result),"tool_call_id": tool_call.id})
        return results
    

    def evaluator_user_prompt(self, reply, message, history):
        user_prompt = f"Aquí está la conversación entre el usuario y el agente: \n\n{history}\n\n"
        user_prompt += f"Aquí está el último mensaje del usuario: \n\n{message}\n\n"
        user_prompt += f"Aquí está la última respuesta del agente: \n\n{reply}\n\n"
        user_prompt += f"Por favor, evalúe la respuesta, indicando si es aceptable y sus comentarios."
        return user_prompt

    def evaluate(self, reply, message, history) -> Evaluation:

        gemini = OpenAI(
            api_key=os.getenv("GOOGLE_API_KEY"), 
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
        )

        evaluator_system_prompt = f"Usted es un evaluador que decide si una respuesta a una pregunta es aceptable. \
        Se le presenta una conversación entre un usuario y un agente. Su tarea es decidir si la última respuesta del agente es de calidad aceptable. \
        El agente desempeña el papel de {self.name} y representa a {self.name} en su sitio web. \
        Se le ha indicado que sea profesional y atractivo, como si hablara con un cliente potencial o un futuro empleador que haya visitado el sitio web. \
        Se le ha proporcionado contexto sobre {self.name} en forma de resumen y datos de LinkedIn. Aquí está la información:"

        evaluator_system_prompt += f"\n\n## Resumen:\n{self.summary}\n\n## Perfil de LinkedIn:\n{self.linkedin}\n\n"
        evaluator_system_prompt += f"Con este contexto, por favor, evalúe la última respuesta, indicando si es aceptable y sus comentarios."

        messages = [{"role": "system", "content": evaluator_system_prompt}] + [{"role": "user", "content": self.evaluator_user_prompt(reply, message, history)}]
        # devolver objeto parseado a clase Evaluation creada anteriormente
        response = gemini.beta.chat.completions.parse(model="gemini-2.0-flash", messages=messages, response_format=Evaluation)
        return response.choices[0].message.parsed # como devuelve un json, ese lo vuelve el objeto de la clase Evaluation
        
    def system_prompt(self):
        system_prompt = f"""Actúas como {self.name}. Respondes preguntas en el sitio web de {self.name}, en particular preguntas relacionadas con la trayectoria profesional, los antecedentes, las habilidades y la experiencia de {self.name}.
            Tu responsabilidad es representar a {self.name} en las interacciones del sitio web con la mayor fidelidad posible.
            Se te proporciona un resumen de la trayectoria profesional y el perfil de LinkedIn de {self.name} que puedes usar para responder preguntas.
            Muestra un tono profesional y atractivo, como si hablaras con un cliente potencial o un futuro empleador que haya visitado el sitio web.
            Si no sabes la respuesta a alguna pregunta, usa la herramienta 'record_unknown_question' para registrar la pregunta que no pudiste responder, incluso si se trata de algo trivial o no relacionado con tu trayectoria profesional.
            Si el usuario participa en una conversación, intenta que se ponga en contacto por correo electrónico; pídele su correo electrónico y regístralo con la herramienta 'record_user_details'."""
        
        system_prompt += f"\n\n## Resumen:\n{self.summary}\n\n## Perfil de LinkedIn:\n{self.linkedin}\n\n"
        system_prompt += f"En este contexto, por favor chatea con el usuario, manteniéndote siempre en el personaje de {self.name}."
        return system_prompt
    
        
    def get_questions_answers(self):
        db = Database()
        preguntas_respuestas = db.obtener_todas()

        mensajes = []
        for pr in preguntas_respuestas:
            mensajes.append({"role": "user", "content": pr.pregunta})
            mensajes.append({"role": "assistant", "content": pr.respuesta})

        return mensajes

    def rerun(self, reply, message, history, feedback):
        updated_system_prompt = self.system_prompt() + f"\n\n## Respuesta anterior rechazada\nAcabas de intentar responder, pero el control de calidad rechazó tu respuesta.\n"
        updated_system_prompt += f"## Has intentado responder:\n{reply}\n\n"
        updated_system_prompt += f"## Razón del rechazo:\n{feedback}\n\n"
        messages = [{"role": "system", "content": updated_system_prompt}] + history + [{"role": "user", "content": message}]
        response = self.openai.chat.completions.create(model=modelo_openai, messages=messages)
        return response.choices[0].message.content

    def chat(self, message, history): 

        # Combina: system_prompt + preguntas y respuestas BD + historial actual + nuevo mensaje del usuario
        messages = (
            [{"role": "system", "content": self.system_prompt()}]
            + self.get_questions_answers()  # de la BD
            + [{"role": "user", "content": message}]
        )

        done = False
        while not done:
            response = self.openai.chat.completions.create(model=modelo_openai, messages=messages, tools=tools)
            reply = response.choices[0].message.content

            # validar respuesta contra otro llm
            evaluation = self.evaluate(reply, message, history)

            if evaluation.is_acceptable:
                print("Has pasado la evaluación - devolviendo respuesta")
                if response.choices[0].finish_reason=="tool_calls":
                    message = response.choices[0].message
                    tool_calls = message.tool_calls
                    results = self.handle_tool_call(tool_calls)
                    messages.append(message)
                    messages.extend(results)
                else:
                    done = True
                return response.choices[0].message.content
            else:
                print("Has fallado la evaluación - reintentando")
                print("feedback del otro agente:" + evaluation.feedback)
                reply = self.rerun(reply, message, history, evaluation.feedback)       
            return reply


# Modelo
class PreguntaRespuesta(BaseModel):
    id: int
    pregunta: str
    respuesta: str

# Clase para manejar la conexión
class Database:
    def __init__(self):
        self.config = {
            'host': os.getenv("host_db"),
            'user': os.getenv("user"),
            'password': os.getenv("password_db"),
            'database': os.getenv("database")
        }
        self.connection = None

    def conectar(self):
        try:
            if not self.connection or not self.connection.is_connected():
                self.connection = mysql.connector.connect(**self.config)
        except Error as e:
            print(f"Error al conectar a la base de datos: {e}")

    def desconectar(self):
        if self.connection and self.connection.is_connected():
            self.connection.close()

    def obtener_todas(self) -> List[PreguntaRespuesta]:
        self.conectar()
        resultados = []
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT id, pregunta, respuesta FROM pregunta_respuestas")
            filas = cursor.fetchall()
            resultados = [PreguntaRespuesta(id=f[0], pregunta=f[1], respuesta=f[2]) for f in filas]
        except Error as e:
            print(f"Error al obtener registros: {e}")
        finally:
            cursor.close()
        return resultados


if __name__ == "__main__":
    me = Me()
    #me.get_questions_answers()
    gr.ChatInterface(me.chat, type="messages").launch()
    
