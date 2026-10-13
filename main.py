from app.bot import run_bot

if __name__ == "__main__":
    try:
        run_bot()
    except Exception as e:
        print("❌ ERROR CRÍTICO AL INICIAR EL SISTEMA:", e)
