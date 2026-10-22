## 1. Qué herramienta de IA usaste y para qué partes.

Use Google antigravity para el desarrollo bajo la metodologia sdd con el modelo gemini 4 argon

## 2. Dos o tres prompts que de verdad te sirvieron, copiados tal cual.

**Prompt 1**

```text
verifica el patrón AAA (Arrange, Act, Assert) en cada test identifica redundancias y oportunidades de optimización, y reduce código duplicado sin perder cobertura, coherencia ni funcionalidad. Dame  mejoras concretas y ejecuta los cambios si son seguros.
```

**Prompt 2**

```text
Modifica el sistema de logs para que se pueda instanciar y reutilizar en toda la aplicación evitando duplicar codigo sin perder información ni trazabilidad respetando la arquitectura hexagonal, DDD
```

## 3. Algo que la IA te entregó mal o incompleto, y cómo te diste cuenta. Esto es lo que más nos interesa.

El log, este hacia una instancia en cada .py lo que hacia que se generara mas codigo de manera inecesaria, lo que se hizo relacionado con el prompt 2 que fuera reutilizable.

## 4. Qué decidiste tú sin consultarle.

La arquitectura de el serivio que fuera hexagonal con una template DDD,
