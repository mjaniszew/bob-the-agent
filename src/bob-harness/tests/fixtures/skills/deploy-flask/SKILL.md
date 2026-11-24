---
name: deploy-flask
description: Deploy the Flask service to production with zero-downtime rolling restarts, health-gated traffic promotion and a documented rollback procedure
---
# Deploy the Flask app
## Steps
1. Build the image
2. Rolling restart

   ```bash
   docker service update --rolling web
   ```

3. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

4. Build the image

   ```bash
   docker compose build web
   ```

5. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

6. Rolling restart

   ```bash
   docker service update --rolling web
   ```

7. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

8. Build the image

   ```bash
   docker compose build web
   ```

9. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

10. Rolling restart

   ```bash
   docker service update --rolling web
   ```

11. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

12. Build the image

   ```bash
   docker compose build web
   ```

13. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

14. Rolling restart

   ```bash
   docker service update --rolling web
   ```

15. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

16. Build the image

   ```bash
   docker compose build web
   ```

17. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

18. Rolling restart

   ```bash
   docker service update --rolling web
   ```

19. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

20. Build the image

   ```bash
   docker compose build web
   ```

21. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

22. Rolling restart

   ```bash
   docker service update --rolling web
   ```

23. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

24. Build the image

   ```bash
   docker compose build web
   ```

25. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

26. Rolling restart

   ```bash
   docker service update --rolling web
   ```

27. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

28. Build the image

   ```bash
   docker compose build web
   ```

29. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

30. Rolling restart

   ```bash
   docker service update --rolling web
   ```

31. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

32. Build the image

   ```bash
   docker compose build web
   ```

33. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

34. Rolling restart

   ```bash
   docker service update --rolling web
   ```

35. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

36. Build the image

   ```bash
   docker compose build web
   ```

37. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

38. Rolling restart

   ```bash
   docker service update --rolling web
   ```

39. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

40. Build the image

   ```bash
   docker compose build web
   ```

41. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

42. Rolling restart

   ```bash
   docker service update --rolling web
   ```

43. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

44. Build the image

   ```bash
   docker compose build web
   ```

45. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

46. Rolling restart

   ```bash
   docker service update --rolling web
   ```

47. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

48. Build the image

   ```bash
   docker compose build web
   ```

49. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

50. Rolling restart

   ```bash
   docker service update --rolling web
   ```

51. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

52. Build the image

   ```bash
   docker compose build web
   ```

53. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

54. Rolling restart

   ```bash
   docker service update --rolling web
   ```

55. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

56. Build the image

   ```bash
   docker compose build web
   ```

57. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

58. Rolling restart

   ```bash
   docker service update --rolling web
   ```

59. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

60. Build the image

   ```bash
   docker compose build web
   ```

61. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

62. Rolling restart

   ```bash
   docker service update --rolling web
   ```

63. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

64. Build the image

   ```bash
   docker compose build web
   ```

65. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

66. Rolling restart

   ```bash
   docker service update --rolling web
   ```

67. Health gate

   ```bash
   curl -fsS http://web.internal/healthz
   ```

68. Build the image

   ```bash
   docker compose build web
   ```

69. Push the image

   ```bash
   docker push registry.internal/web:prod
   ```

```bash
./scripts/deploy --profile prod
```
## Pitfalls
- NEVER skip the health gate
- the registry needs the prod token
- wait 30s between restarts
