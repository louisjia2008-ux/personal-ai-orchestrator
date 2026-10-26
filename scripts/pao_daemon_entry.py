import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--pao-file-tool":
        from personal_ai_orchestrator.pi_file_tools import main

        raise SystemExit(main(sys.argv[2:]))
    from personal_ai_orchestrator.product_daemon import main

    raise SystemExit(main())
