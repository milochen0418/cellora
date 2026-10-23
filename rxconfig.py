import reflex as rx
from dotenv import load_dotenv

# Settings for local runs (see .env.template); the App Store passes them as the container's environment.
load_dotenv()

config = rx.Config(
    app_name="cellora",
    plugins=[
        rx.plugins.TailwindV3Plugin(
            config={
                "plugins": ["@tailwindcss/typography@0.5.20"],
                # Reflex 0.10 compiles memoized components into .web/app_components,
                # which the plugin's default content globs miss, so their classes
                # would never be generated.
                "content": [
                    "./app/**/*.{js,ts,jsx,tsx}",
                    "./utils/**/*.{js,ts,jsx,tsx}",
                    "./app_components/**/*.{js,ts,jsx,tsx}",
                ],
            },
        ),
        # Always light: the notebook paints white cells, editors and outputs.
        rx.plugins.RadixThemesPlugin(theme=rx.theme(appearance="light", accent_color="indigo", radius="medium")),
    ],
    disable_plugins=[rx.plugins.SitemapPlugin],
)
