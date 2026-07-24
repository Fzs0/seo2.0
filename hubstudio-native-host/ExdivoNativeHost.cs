using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Web.Script.Serialization;

public static class ExdivoNativeHost
{
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    private const string Backend = "http://127.0.0.1:8000";

    public static void Main()
    {
        Stream input = Console.OpenStandardInput();
        Stream output = Console.OpenStandardOutput();
        while (true)
        {
            byte[] lengthBytes = ReadExact(input, 4);
            if (lengthBytes == null) return;
            int length = BitConverter.ToInt32(lengthBytes, 0);
            if (length <= 0 || length > 1024 * 1024)
            {
                Write(output, new { ok = false, error = "Invalid native message length" });
                return;
            }
            byte[] payload = ReadExact(input, length);
            if (payload == null) return;
            try
            {
                Dictionary<string, object> message =
                    Json.Deserialize<Dictionary<string, object>>(Encoding.UTF8.GetString(payload));
                Write(output, Handle(message));
            }
            catch (Exception error)
            {
                Write(output, new { ok = false, error = error.Message });
            }
        }
    }

    private static object Handle(Dictionary<string, object> message)
    {
        string type = Value(message, "type");
        string path = Value(message, "path");
        if (type != "http" ||
            !(path == "/api/health" || path.StartsWith("/api/v1/social/extension/")))
            return new { ok = false, error = "Native request is outside the allowlist" };

        string method = Value(message, "method");
        if (method != "GET" && method != "POST")
            return new { ok = false, error = "Unsupported native request method" };

        HttpWebRequest request = (HttpWebRequest)WebRequest.Create(Backend + path);
        request.Method = method;
        request.Timeout = 15000;
        request.ContentType = "application/json";
        string token = Value(message, "token");
        if (!String.IsNullOrEmpty(token)) request.Headers["Authorization"] = "Bearer " + token;
        string body = Value(message, "body");
        if (method == "POST" && !String.IsNullOrEmpty(body))
        {
            byte[] bytes = Encoding.UTF8.GetBytes(body);
            request.ContentLength = bytes.Length;
            using (Stream stream = request.GetRequestStream()) stream.Write(bytes, 0, bytes.Length);
        }
        try
        {
            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
            using (StreamReader reader = new StreamReader(response.GetResponseStream()))
                return new { ok = true, status = (int)response.StatusCode, body = reader.ReadToEnd() };
        }
        catch (WebException error)
        {
            HttpWebResponse response = error.Response as HttpWebResponse;
            string responseBody = "";
            if (response != null)
                using (StreamReader reader = new StreamReader(response.GetResponseStream()))
                    responseBody = reader.ReadToEnd();
            return new {
                ok = false,
                status = response == null ? 0 : (int)response.StatusCode,
                error = String.IsNullOrEmpty(responseBody) ? error.Message : responseBody
            };
        }
    }

    private static string Value(Dictionary<string, object> message, string key)
    {
        object value;
        return message.TryGetValue(key, out value) && value != null ? value.ToString() : "";
    }

    private static byte[] ReadExact(Stream stream, int count)
    {
        byte[] buffer = new byte[count];
        int offset = 0;
        while (offset < count)
        {
            int read = stream.Read(buffer, offset, count - offset);
            if (read == 0) return null;
            offset += read;
        }
        return buffer;
    }

    private static void Write(Stream stream, object value)
    {
        byte[] payload = Encoding.UTF8.GetBytes(Json.Serialize(value));
        byte[] length = BitConverter.GetBytes(payload.Length);
        stream.Write(length, 0, length.Length);
        stream.Write(payload, 0, payload.Length);
        stream.Flush();
    }
}
