import sys
import httpx
import asyncio

TOKEN = "8691918681:AAFsl1Y2ILiawqOyw2mgs7IQjAUvzAGa3-g"

async def main():
    async with httpx.AsyncClient() as client:
        r = await client.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates")
        data = r.json()
        
        topics = {}
        if "result" in data:
            for update in data["result"]:
                msg = update.get("message", {})
                
                # Check if it has a forum topic created action
                if "forum_topic_created" in msg:
                    topic_name = msg["forum_topic_created"]["name"]
                    thread_id = msg["message_thread_id"]
                    topics[topic_name] = thread_id
                    continue
                    
                # If someone sent a message inside the topic
                if "message_thread_id" in msg and msg.get("is_topic_message", False):
                    thread_id = msg["message_thread_id"]
                    if thread_id not in topics.values():
                        topics[f"Unknown Topic/Message in {thread_id}"] = thread_id

        print(f"Topicos Encontrados: {topics}")
        if not topics:
            print("Nenhum tópico encontrado ou não houve mensagens recentes. "
                  "Por favor, mande um 'Oi' dentro de cada tópico criado para "
                  "o bot registrar os IDs!")

if __name__ == "__main__":
    asyncio.run(main())
